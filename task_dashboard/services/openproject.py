import base64
import logging
import re
from http import HTTPStatus
from typing import Any

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone as django_timezone

from task_dashboard.services.base import BaseService
from task_dashboard.users.models import GlobalSetting

logger = logging.getLogger(__name__)


class OpenProjectService(BaseService):
    STATUS_MAPPING: dict[str, list[str]] = {
        "open": ["new", "open", "to do", "progress", "schedule"],
        "closed": ["closed", "done", "resolved", "reject"],
    }
    PRIORITY_MAPPING: dict[str, list[str]] = {
        "Critical": ["immediate", "critical"],
        "High": ["high", "urgent"],
        "Low": ["low"],
        "Medium": [],  # Fallback
    }

    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url.rstrip("/")
        self.api_key = config.api_token
        self.host_header = getattr(settings, "OPENPROJECT_HOST_HEADER", None)

    def _get_headers(self):
        auth_str = f"apikey:{self.api_key}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/json",
        }
        if self.host_header:
            headers["Host"] = self.host_header
        return headers

    async def get_tasks_async(self, *, force_refresh=False):
        cache_key = f"openproject_{self.config.id}_active_packages_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        # Fetch global setting for fallback customer name
        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_tasks: list[dict[str, Any]] = []

            try:
                # Fetch all open work packages
                await self._fetch_work_packages(
                    client, normalized_tasks, user_map, company_name
                )
            except httpx.HTTPError:
                logger.exception("Error fetching OpenProject data")
                cache.delete(cache_key)
                raise
            except Exception:
                logger.exception("Unexpected error fetching OpenProject data")
                cache.delete(cache_key)
                raise
            else:
                cache.set(cache_key, normalized_tasks, timeout=300)
                return normalized_tasks

    async def get_single_task_async(self, task):
        if not self.api_key or not task.url:
            return None

        # Extract ID from URL (e.g. /work_packages/123)
        match = re.search(r"work_packages/(\d+)", task.url)
        if not match:
            return None

        task_id = match.group(1)
        url = f"{self.base_url}/api/v3/work_packages/{task_id}"

        # Fetch global setting for fallback customer name
        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_tasks: list[dict[str, Any]] = []
            try:
                resp = await client.get(url, headers=self._get_headers(), timeout=20.0)
                resp.raise_for_status()
                item = resp.json()
                self._process_work_package(
                    item, normalized_tasks, user_map, company_name
                )
                return normalized_tasks[0] if normalized_tasks else None
            except Exception:
                logger.exception("Error fetching single OpenProject task %s", task_id)
                return None

    async def _get_user_map(self, client: httpx.AsyncClient):
        cache_key = f"op_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v3/users"
            offset = 1
            page_size = 100

            while True:
                resp = await client.get(
                    url,
                    headers=self._get_headers(),
                    params={"offset": offset, "pageSize": page_size},
                    timeout=10.0,
                )
                if resp.status_code == HTTPStatus.OK:
                    elements = resp.json().get("_embedded", {}).get("elements", [])
                    if not elements:
                        break

                    for u in elements:
                        uid = u.get("id")
                        email = u.get("email")
                        login = u.get("login")
                        if uid:
                            final_email = email if email else f"{login}@placeholder"
                            user_map[uid] = final_email

                    if len(elements) < page_size:
                        break
                    offset += 1
                elif resp.status_code == HTTPStatus.FORBIDDEN:
                    logger.warning(
                        "OpenProject User Map access forbidden (403). "
                        "Credentials may lack permission to list users."
                    )
                    break
                else:
                    break

            cache.set(cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("OpenProject User Map failed: %s", e)
        return user_map

    async def _fetch_work_packages(
        self, client, normalized_tasks, user_map, company_name
    ):
        url = f"{self.base_url}/api/v3/work_packages"
        offset = 1
        page_size = 100

        try:
            while True:
                params = {
                    "offset": offset,
                    "pageSize": page_size,
                    "sortBy": '[["updatedAt","desc"]]',
                }
                resp = await client.get(
                    url, params=params, headers=self._get_headers(), timeout=20.0
                )
                resp.raise_for_status()

                data = resp.json()
                elements = data.get("_embedded", {}).get("elements", [])
                if not elements:
                    break

                for item in elements:
                    self._process_work_package(
                        item, normalized_tasks, user_map, company_name
                    )

                if len(elements) < page_size:
                    break
                offset += 1
        except httpx.HTTPError as e:
            logger.warning("Failed to fetch OpenProject work packages: %s", e)
            raise

    def _process_work_package(self, item, normalized_tasks, user_map, company_name):
        links = item.get("_links", {})

        assignee_link = links.get("assignee", {})
        assignee_href = assignee_link.get("href", "")
        assignee_name = assignee_link.get("title", "-")
        assignee_email = None

        if assignee_href:
            try:
                uid = int(assignee_href.split("/")[-1])
                assignee_email = user_map.get(uid)
            except ValueError:
                pass

        status_title = links.get("status", {}).get("title", "Unknown")
        mapped_status = self._map_status(status_title)

        priority_title = links.get("priority", {}).get("title", "Medium")
        mapped_priority = self._map_priority(priority_title)

        project_link = links.get("project", {})
        project_title = project_link.get("title", "Project")
        project_href = project_link.get("href", "")

        customer_name = company_name
        group_name = f"{company_name}/{project_title}"
        project_only = project_title

        if " ⏳ " in project_title:
            parts = project_title.split(" ⏳ ", 1)
            project_only = parts[0].strip()
            customer_name = parts[1].strip()
            group_name = f"{customer_name}/{project_only}"

        project_id = project_href.split("/")[-1] if project_href else None

        normalized_tasks.append(
            {
                "id": f"OP-{item.get('id')}",
                "title": item.get("subject"),
                "status": mapped_status,
                "priority": mapped_priority,
                "original_status": status_title,
                "original_priority": priority_title,
                "origin": self.config.name,
                "customer": customer_name,
                "group": group_name,
                "owner": assignee_name,
                "owner_email": assignee_email,
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt"),
                "due_date": item.get("dueDate"),
                "url": f"{self.base_url}/work_packages/{item.get('id')}",
                "extra_info": {
                    "project_id": project_id,
                    "project_name": project_only,
                    "full_project_title": project_title,
                },
            }
        )

    def check_health(self):
        start = django_timezone.now()
        if not self.api_key:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key",
            }
        try:
            response = httpx.get(
                f"{self.base_url}/api/v3/users/me",
                headers=self._get_headers(),
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
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
