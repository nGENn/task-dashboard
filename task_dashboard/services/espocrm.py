import asyncio
import logging
import re
from http import HTTPStatus

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

from task_dashboard.users.models import GlobalSetting

logger = logging.getLogger(__name__)


class EspoService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.api_key = config.api_token
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def get_tasks(self, *, force_refresh=False):
        return asyncio.run(self.get_tasks_async(force_refresh=force_refresh))

    async def get_tasks_async(self, *, force_refresh=False):
        cache_key = f"espo_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_tasks = []

            try:
                # Fetch Cases and Tasks in parallel
                base_params = {"orderBy": "createdAt", "order": "desc"}
                # Use a context dictionary to keep argument count down (PLR0913)
                ctx = {
                    "target": normalized_tasks,
                    "user_map": user_map,
                    "company_name": company_name,
                }
                await asyncio.gather(
                    self._fetch_entity(
                        client,
                        f"{self.base_url}/api/v1/Case",
                        "Case",
                        base_params,
                        ctx,
                    ),
                    self._fetch_entity(
                        client,
                        f"{self.base_url}/api/v1/Task",
                        "Task",
                        base_params,
                        ctx,
                    ),
                )
            except httpx.HTTPError:
                logger.exception("Error fetching EspoCRM data")
                return []
            else:
                cache.set(cache_key, normalized_tasks, timeout=300)
                return normalized_tasks

    def get_single_task(self, task):
        return asyncio.run(self.get_single_task_async(task))

    async def get_single_task_async(self, task):
        if not self.api_key or not task.url:
            return None

        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        # Extract entity type and ID from URL (e.g. /#Task/view/123)
        match = re.search(r"#([^/]+)/view/([^/]+)", task.url)
        if not match:
            return None

        entity_type = match.group(1)
        task_id = match.group(2)
        url = f"{self.base_url}/api/v1/{entity_type}/{task_id}"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            normalized_tasks = []

            try:
                resp = await client.get(url, headers=self.headers, timeout=15.0)
                resp.raise_for_status()
                item = resp.json()
                self._process_items(
                    [item], entity_type, normalized_tasks, user_map, company_name
                )
                return normalized_tasks[0] if normalized_tasks else None
            except Exception:
                logger.exception("Error fetching single EspoCRM task %s", task_id)
                return None

    async def _get_user_map(self, client: httpx.AsyncClient):
        cache_key = f"espo_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v1/User"
            params = {"maxSize": 200, "select": "id,emailAddress,userName"}
            resp = await client.get(
                url, headers=self.headers, params=params, timeout=10.0
            )
            if resp.status_code == HTTPStatus.OK:
                users = resp.json().get("list", [])
                for u in users:
                    uid = u.get("id")
                    email = u.get("emailAddress")
                    if uid:
                        user_map[uid] = (
                            email if email else f"{u.get('userName')}@placeholder"
                        )
            cache.set(cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("Espo User Map failed: %s", e)
        return user_map

    async def _fetch_entity(self, client, url, entity_type, params, ctx):
        try:
            # Fetch first page
            max_size = 100
            request_params = {**params, "offset": 0, "maxSize": max_size}
            resp = await client.get(
                url, headers=self.headers, params=request_params, timeout=15.0
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("list", [])
            if not items:
                return

            self._process_items(
                items,
                entity_type,
                ctx["target"],
                ctx["user_map"],
                ctx["company_name"],
            )

            # If page 1 was full, fetch page 2 concurrently
            if len(items) == max_size:
                request_params["offset"] = max_size
                resp2 = await client.get(
                    url, headers=self.headers, params=request_params, timeout=15.0
                )
                if resp2.status_code == HTTPStatus.OK:
                    data2 = resp2.json()
                    self._process_items(
                        data2.get("list", []),
                        entity_type,
                        ctx["target"],
                        ctx["user_map"],
                        ctx["company_name"],
                    )

        except httpx.HTTPError as e:
            logger.warning("Failed to fetch Espo %s: %s", entity_type, e)

    def _process_items(self, items, entity_type, target_list, user_map, company_name):
        for item in items:
            owner_id = item.get("assignedUserId")
            owner_email = user_map.get(owner_id)
            target_list.append(
                {
                    "id": (
                        f"ESPO-{entity_type[0]}-{item.get('number') or item.get('id')}"
                    ),
                    "title": item.get("name"),
                    "status": self._map_status(item.get("status")),
                    "priority": self._map_priority(item.get("priority", "Medium")),
                    "origin": self.config.name,
                    "customer": item.get("accountName") or company_name,
                    "group": entity_type,
                    "owner": item.get("assignedUserName", "-"),
                    "owner_email": owner_email,
                    "created_at": item.get("createdAt"),
                    "updated_at": item.get("modifiedAt"),
                    "due_date": (item.get("dueDate") or item.get("dateEnd")),
                    "url": f"{self.base_url}/#{entity_type}/view/{item.get('id')}",
                    "extra_info": {
                        "entity_type": entity_type,
                    },
                }
            )

    def _map_status(self, espo_status):
        s = str(espo_status).lower()
        if s in ["new", "assigned", "pending", "not started", "in progress"]:
            return "open"
        if s in ["closed", "rejected", "merged", "completed"]:
            return "closed"
        return "pending"

    def _map_priority(self, priority_text):
        p = str(priority_text).lower()
        if any(x in p for x in ["urgent", "critical"]):
            return "Critical"
        if any(x in p for x in ["high"]):
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
                f"{self.base_url}/api/v1/App/user", headers=self.headers, timeout=3.0
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
