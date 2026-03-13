import asyncio
import datetime
import logging
from http import HTTPStatus
from urllib.parse import quote
from urllib.parse import urlparse

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

from task_dashboard.users.models import GlobalSetting

logger = logging.getLogger(__name__)


class GitLabService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.token = config.api_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get_tasks(self, *, force_refresh=False):
        return asyncio.run(self.get_tasks_async(force_refresh=force_refresh))

    async def get_tasks_async(self, *, force_refresh=False):
        cache_key = f"gitlab_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.token:
            return []

        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_items = []

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
                return []
            else:
                cache.set(cache_key, normalized_items, timeout=300)
                return normalized_items

    def get_single_task(self, task):
        return asyncio.run(self.get_single_task_async(task))

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
            normalized_items = []
            ctx = {"target": normalized_items, "user_map": user_map}

            try:
                # We need a slightly custom fetch because _fetch_and_normalize
                # expects a list
                resp = await client.get(url, headers=self.headers, timeout=15.0)
                resp.raise_for_status()

                item = resp.json()
                # Mock a list wrapper to reuse _fetch_and_normalize logic if we
                # wanted, but it's simpler to just do what it does inline,
                # or monkey patch the response:
                # To reuse code, we can just process this single item:

                assignee = item.get("assignee") or {}
                if not assignee and item.get("assignees"):
                    assignee = item.get("assignees")[0]

                author = item.get("author", {})
                owner_name = assignee.get("name") or author.get("name") or "-"
                owner_email = (
                    ctx["user_map"].get(assignee.get("id") or author.get("id")) or ""
                )

                group_name = project_path

                return {
                    "id": f"GL-{item_type[0]}-{item.get('iid')}",
                    "title": item.get("title"),
                    "status": "open" if item.get("state") == "opened" else "resolved",
                    "priority": "Medium",
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
        cache_key = f"gitlab_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            # We fetch up to 100 users for mapping
            url = f"{self.base_url}/api/v4/users"
            resp = await client.get(
                url, headers=self.headers, params={"per_page": 100}, timeout=10.0
            )
            if resp.status_code == HTTPStatus.OK:
                for u in resp.json():
                    uid = u.get("id")
                    email = u.get("email")
                    public_email = u.get("public_email")
                    if uid:
                        user_map[uid] = email if email else public_email
            cache.set(cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("GitLab User Map failed: %s", e)
        return user_map

    async def _fetch_and_normalize(self, client, url, item_type, ctx, params=None):
        try:
            resp = await client.get(
                url, headers=self.headers, params=params, timeout=15.0
            )
            resp.raise_for_status()

            for item in resp.json():
                assignee = item.get("assignee") or {}
                if not assignee and item.get("assignees"):
                    assignee = item.get("assignees")[0]

                author = item.get("author", {})
                owner_name = assignee.get("name") or author.get("name") or "-"
                owner_email = ctx["user_map"].get(
                    assignee.get("id") or author.get("id")
                )

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

                ctx["target"].append(
                    {
                        "id": f"GL-{item_type[0]}-{item.get('iid')}",
                        "title": item.get("title"),
                        "status": "open",
                        "priority": "Medium",
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
                )
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch GitLab %s: %s", item_type, e)

    def _format_date(self, dt_str):
        if not dt_str:
            return ""
        try:
            # Validate it's a valid ISO string
            datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return dt_str
        else:
            return dt_str

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
