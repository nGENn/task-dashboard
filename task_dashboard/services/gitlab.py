import asyncio
import logging
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

from task_dashboard.services.base import BaseService
from task_dashboard.users.models import GlobalSetting

logger = logging.getLogger(__name__)


class GitLabService(BaseService):
    STATUS_MAPPING: dict[str, list[str]] = {
        "open": ["opened"],
        "closed": ["closed", "merged", "locked"],
    }
    PRIORITY_MAPPING: dict[str, list[str]] = {
        "Critical": [],
        "High": [],
        "Low": [],
        "Medium": [],  # Default
    }

    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.token = config.api_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    async def get_tasks_async(self, *, force_refresh=False):
        cache_key = f"gitlab_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = await cache.aget(cache_key)
            if cached_data:
                return cached_data

        if not self.token:
            return []

        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_items: list[dict[str, Any]] = []

            try:
                # Fetch Issues and Merge Requests in parallel
                base_params = {
                    "state": "opened",
                    "scope": "all",
                    "order_by": "updated_at",
                }
                # Use context dict for PLR0913
                ctx = {
                    "target": normalized_items,
                    "user_map": user_map,
                    "company_name": company_name,
                }
                await asyncio.gather(
                    self._fetch_and_normalize(
                        client,
                        f"{self.base_url}/api/v4/issues",
                        "Issue",
                        ctx,
                        params=base_params,
                    ),
                    self._fetch_and_normalize(
                        client,
                        f"{self.base_url}/api/v4/merge_requests",
                        "MR",
                        ctx,
                        params=base_params,
                    ),
                )
            except httpx.HTTPError:
                logger.exception("Error fetching GitLab tasks")
                await cache.adelete(cache_key)
                raise
            except Exception:
                logger.exception("Unexpected error fetching GitLab tasks")
                await cache.adelete(cache_key)
                raise
            else:
                await cache.aset(cache_key, normalized_items, timeout=300)
                return normalized_items

    async def get_single_task_async(self, task):
        if not self.token or not task.url:
            return None

        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        # Parse project path and IID from URL
        # e.g., /group/project/-/issues/24
        path = urlparse(task.url).path

        if "/-/issues/" in path:
            parts = path.split("/-/issues/")
            item_type = "Issue"
            api_type = "issues"
        elif "/-/merge_requests/" in path:
            parts = path.split("/-/merge_requests/")
            item_type = "MR"
            api_type = "merge_requests"
        else:
            return None

        expected_parts = 2
        if len(parts) != expected_parts:
            return None

        project_path = parts[0].strip("/")
        iid = parts[1].strip("/")
        encoded_project = quote(project_path, safe="")

        url = f"{self.base_url}/api/v4/projects/{encoded_project}/{api_type}/{iid}"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_items: list[dict[str, Any]] = []
            ctx = {"target": normalized_items, "user_map": user_map}

            try:
                resp = await client.get(url, headers=self.headers, timeout=15.0)
                resp.raise_for_status()

                item = resp.json()

                assignee = item.get("assignee") or {}
                if not assignee and item.get("assignees"):
                    assignee = item.get("assignees")[0]

                author = item.get("author", {})
                owner_name = assignee.get("name") or author.get("name") or "-"
                owner_email = (
                    ctx["user_map"].get(assignee.get("id") or author.get("id")) or ""
                )

                group_name = project_path
                original_status = item.get("state") or "unknown"
                original_priority = "Medium"

                return {
                    "id": f"GL-{item_type[0]}-{item.get('iid')}",
                    "title": item.get("title"),
                    "status": self._map_status(original_status),
                    "priority": self._map_priority(original_priority),
                    "original_status": original_status,
                    "original_priority": original_priority,
                    "origin": self.config.name,
                    "customer": company_name,
                    "group": group_name,
                    "owner": owner_name,
                    "owner_email": owner_email,
                    "created_at": self._format_date(item.get("created_at")),
                    "updated_at": self._format_date(item.get("updated_at")),
                    "due_date": None,
                    "url": item.get("web_url", task.url),
                    "extra_info": {
                        "gitlab_id": item.get("id"),
                        "project_id": item.get("project_id"),
                        "type": item_type,
                    },
                }
            except Exception:
                logger.exception("Error fetching single GitLab task %s", task.url)
                return None

    async def _get_user_map(self, client: httpx.AsyncClient):
        async def fetch_users():
            user_map = {}
            url = f"{self.base_url}/api/v4/users"
            page = 1
            per_page = 100
            while True:
                resp = await client.get(
                    url,
                    headers=self.headers,
                    params={"page": page, "per_page": per_page},
                    timeout=10.0,
                )
                if resp.status_code == HTTPStatus.OK:
                    elements = resp.json()
                    if not elements:
                        break
                    for u in elements:
                        uid = u.get("id")
                        if uid:
                            user_map[uid] = u.get("email") or u.get("public_email")
                    if len(elements) < per_page:
                        break
                    page += 1
                else:
                    break
            return user_map

        return await self._fetch_and_cache(
            cache_key=f"gitlab_{self.config.id}_user_map",
            timeout_secs=3600,
            fetch_fn=fetch_users,
            error_msg="GitLab User Map failed",
        )

    async def _fetch_and_normalize(self, client, url, item_type, ctx, params=None):
        if params is None:
            params = {}

        page = 1
        per_page = 100
        params["per_page"] = per_page

        try:
            while True:
                params["page"] = page
                resp = await client.get(
                    url, headers=self.headers, params=params, timeout=15.0
                )
                resp.raise_for_status()

                elements = resp.json()
                if not elements:
                    break

                for item in elements:
                    ctx["target"].append(self._normalize_item(item, item_type, ctx))

                if len(elements) < per_page:
                    break
                page += 1
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch GitLab %s: %s", item_type, e)
            raise

    def _normalize_item(self, item, item_type, ctx):
        assignee = item.get("assignee") or {}
        if not assignee and item.get("assignees"):
            assignee = item.get("assignees")[0]

        author = item.get("author", {})
        owner_name = assignee.get("name") or author.get("name") or "-"
        owner_email = ctx["user_map"].get(assignee.get("id") or author.get("id"))

        group_name = item_type
        references = item.get("references")
        if references and "full" in references:
            full_ref = references["full"]
            if "#" in full_ref:
                group_name = full_ref.split("#")[0]
            elif "!" in full_ref:
                group_name = full_ref.split("!")[0]
        else:
            web_url = item.get("web_url", "")
            if web_url:
                path = urlparse(web_url).path
                if "/-/issues/" in path:
                    group_name = path.split("/-/issues/")[0].strip("/")
                elif "/-/merge_requests/" in path:
                    group_name = path.split("/-/merge_requests/")[0].strip("/")

        return {
            "id": f"GL-{item_type[0]}-{item.get('iid')}",
            "title": item.get("title"),
            "status": self._map_status(item.get("state")),
            "priority": self._map_priority("Medium"),
            "original_status": item.get("state") or "unknown",
            "original_priority": "Medium",
            "origin": self.config.name,
            "customer": ctx["company_name"],
            "group": group_name,
            "owner": owner_name,
            "owner_email": owner_email,
            "created_at": self._format_date(item.get("created_at")),
            "updated_at": self._format_date(item.get("updated_at")),
            "due_date": self._format_date(item.get("due_date")),
            "url": item.get("web_url"),
            "extra_info": {
                "project_id": item.get("project_id"),
            },
        }

    def check_health(self):
        start = django_timezone.now()
        if not self.token:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Token",
            }
        try:
            response = httpx.get(
                f"{self.base_url}/api/v4/user", headers=self.headers, timeout=5.0
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("%s Auth Failed: %s", self.config.name, e)
            return {
                "name": self.config.name,
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }
        else:
            latency = int((django_timezone.now() - start).total_seconds() * 1000)
            return {
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }
