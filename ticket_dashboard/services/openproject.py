import asyncio
import base64
import logging
from http import HTTPStatus

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


class OpenProjectService:
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

    def get_tickets(self, *, force_refresh=False):
        return asyncio.run(self.get_tickets_async(force_refresh=force_refresh))

    async def get_tickets_async(self, *, force_refresh=False):
        cache_key = f"openproject_{self.config.id}_active_packages_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_tickets = []

            try:
                await self._fetch_work_packages(client, normalized_tickets, user_map)
            except httpx.HTTPError:
                logger.exception("Error fetching OpenProject packages")
                return []
            else:
                cache.set(cache_key, normalized_tickets, timeout=300)
                return normalized_tickets

    async def _get_user_map(self, client: httpx.AsyncClient):
        cache_key = f"op_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v3/users"
            resp = await client.get(
                url, headers=self._get_headers(), params={"pageSize": 100}, timeout=10.0
            )
            if resp.status_code == HTTPStatus.OK:
                elements = resp.json().get("_embedded", {}).get("elements", [])
                for u in elements:
                    uid = u.get("id")
                    email = u.get("email")
                    login = u.get("login")
                    if uid:
                        final_email = email if email else f"{login}@placeholder"
                        user_map[uid] = final_email
            elif resp.status_code == HTTPStatus.FORBIDDEN:
                logger.warning(
                    "OpenProject User Map access forbidden (403). "
                    "Credentials may lack permission to list users."
                )
            cache.set(cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("OpenProject User Map failed: %s", e)
        return user_map

    async def _fetch_work_packages(self, client, normalized_tickets, user_map):
        url = f"{self.base_url}/api/v3/work_packages"
        page_size = 100

        # Fetch first page
        params = {
            "offset": 1,
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
            return

        for item in elements:
            self._process_work_package(item, normalized_tickets, user_map)

        # Fetch page 2 concurrently if page 1 was full
        if len(elements) == page_size:
            params["offset"] = 2
            resp2 = await client.get(
                url, params=params, headers=self._get_headers(), timeout=20.0
            )
            if resp2.status_code == HTTPStatus.OK:
                elements2 = resp2.json().get("_embedded", {}).get("elements", [])
                for item in elements2:
                    self._process_work_package(item, normalized_tickets, user_map)

    def _process_work_package(self, item, normalized_tickets, user_map):
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

        project_link = links.get("project", {})
        project_title = project_link.get("title", "Project")
        project_href = project_link.get("href", "")

        customer_name = "all"
        group_name = "all"
        project_only = project_title

        if " ⏳ " in project_title:
            parts = project_title.split(" ⏳ ", 1)
            project_only = parts[0].strip()
            customer_name = parts[1].strip()
            group_name = f"{customer_name}/{project_only}"

        project_id = project_href.split("/")[-1] if project_href else None

        normalized_tickets.append(
            {
                "id": f"OP-{item.get('id')}",
                "title": item.get("subject"),
                "status": mapped_status,
                "priority": self._map_priority(
                    links.get("priority", {}).get("title", "Medium")
                ),
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

    def _map_status(self, status_text):
        s = str(status_text).lower()
        open_keywords = ["new", "open", "to do", "progress", "schedule"]
        if any(x in s for x in open_keywords):
            return "open"
        if any(x in s for x in ["closed", "done", "resolved", "reject"]):
            return "closed"
        return "pending"

    def _map_priority(self, priority_text):
        p = str(priority_text).lower()
        if any(x in p for x in ["immediate", "critical"]):
            return "Critical"
        if any(x in p for x in ["high", "urgent"]):
            return "High"
        if any(x in p for x in ["low"]):
            return "Low"
        return "Medium"

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
