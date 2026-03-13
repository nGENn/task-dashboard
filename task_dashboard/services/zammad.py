import asyncio
import logging
import re
from datetime import datetime
from http import HTTPStatus

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

from task_dashboard.users.models import GlobalSetting

logger = logging.getLogger(__name__)


class ZammadService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.token = config.api_token

        self.headers = {
            "Authorization": f"Token token={self.token}",
            "Content-Type": "application/json",
        }

    async def _get_user_map(self, client: httpx.AsyncClient):
        """Map Zammad User ID -> Email"""
        cache_key = f"zammad_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v1/users"
            # Increased timeout for user map
            resp = await client.get(
                url,
                headers=self.headers,
                params={"per_page": 250},
                timeout=30.0,
            )
            if resp.status_code == HTTPStatus.OK:
                for u in resp.json():
                    uid = u.get("id")
                    email = u.get("email")
                    name = (
                        f"{u.get('firstname', '')} {u.get('lastname', '')}"
                    ).strip() or u.get("login")
                    if uid:
                        user_map[uid] = {"email": email, "name": name}

            cache.set(cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("Zammad User Map failed: %s", e)
        return user_map

    def get_tasks(self, *, force_refresh=False):
        return asyncio.run(self.get_tasks_async(force_refresh=force_refresh))

    async def get_tasks_async(self, *, force_refresh=False):
        cache_key = f"zammad_{self.config.id}_active_tasks_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.debug("Returning cached Zammad data")
                return cached_data

        if not self.base_url or not self.token:
            logger.warning("Zammad credentials not found.")
            return []

        # Fetch global setting for fallback customer name
        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)

            try:
                raw_tasks = await self._fetch_all_tasks_async(client)
                normalized_tasks = self._normalize_tasks(
                    raw_tasks, user_map, company_name
                )
            except httpx.HTTPError:
                logger.exception("Error fetching Zammad tasks")
                # Instead of returning [], we raise so fetch_service_tasks knows it
                # failed (PLR0913 workaround)
                raise
            except Exception:
                logger.exception("Unexpected error fetching Zammad tasks")
                raise
            else:
                cache.set(cache_key, normalized_tasks, timeout=300)
                return normalized_tasks

    def get_single_task(self, task):
        return asyncio.run(self.get_single_task_async(task))

    async def get_single_task_async(self, task):
        if not self.base_url or not self.token:
            return None

        # Extract native Zammad ID from URL (e.g. /#ticket/zoom/1234)
        match = re.search(r"#ticket/zoom/(\d+)", task.url)
        if not match:
            logger.error("Could not extract Zammad ID from URL: %s", task.url)
            return None

        ticket_id = match.group(1)
        url = f"{self.base_url}/api/v1/tickets/{ticket_id}"

        # Fetch global setting for fallback customer name
        global_setting = await GlobalSetting.objects.afirst()
        company_name = global_setting.company_name if global_setting else "Internal"

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)
            try:
                resp = await client.get(
                    url, headers=self.headers, params={"expand": "true"}, timeout=45.0
                )
                resp.raise_for_status()
                raw_task = resp.json()
                normalized = self._normalize_tasks([raw_task], user_map, company_name)
                return normalized[0] if normalized else None
            except Exception:
                logger.exception("Error fetching single Zammad task %s", ticket_id)
                return None

    async def _fetch_all_tasks_async(self, client: httpx.AsyncClient):
        url = f"{self.base_url}/api/v1/tickets"
        raw_tasks = []
        per_page = 100

        # Fetch first page to see how many we have
        first_page_params = {
            "expand": "true",
            "page": 1,
            "per_page": per_page,
            "order_by": "updated_at",
            "sort_by": "desc",
        }
        # Increased timeout to 45.0s to avoid ReadTimeout
        resp = await client.get(
            url, headers=self.headers, params=first_page_params, timeout=45.0
        )
        resp.raise_for_status()
        data = resp.json()

        # Zammad API might return list directly or dict with 'tickets'
        first_page_tasks = data.get("tickets", []) if isinstance(data, dict) else data
        if not first_page_tasks:
            return []

        raw_tasks.extend(first_page_tasks)

        # If we have a full first page, fetch subsequent pages.
        # SAFEGUARD: Use a Semaphore to limit concurrency and avoid overwhelming Zammad
        if len(first_page_tasks) == per_page:
            semaphore = asyncio.Semaphore(2)  # Max 2 concurrent page requests

            async def fetch_page(page_num):
                async with semaphore:
                    params = {**first_page_params, "page": page_num}
                    r = await client.get(
                        url, headers=self.headers, params=params, timeout=45.0
                    )
                    r.raise_for_status()
                    p_data = r.json()
                    return (
                        p_data.get("tickets", [])
                        if isinstance(p_data, dict)
                        else p_data
                    )

            tasks = [fetch_page(page) for page in range(2, 11)]
            responses = await asyncio.gather(*tasks, return_exceptions=True)

            for res in responses:
                if isinstance(res, list):
                    raw_tasks.extend(res)
                elif isinstance(res, Exception):
                    logger.warning("Failed to fetch Zammad page: %s", res)
                    # We continue even if one page fails, as we have other data

        return raw_tasks

    def _normalize_tasks(self, raw_tasks, user_map, company_name):
        normalized_tasks = []
        for task in raw_tasks:
            owner_id = task.get("owner_id")
            user_info = user_map.get(owner_id, {})
            owner_name = user_info.get("name", "Unassigned")
            owner_email = user_info.get("email")

            normalized_tasks.append(
                {
                    "id": f"ZAM-{task.get('number')}",
                    "title": task.get("title"),
                    "status": self._map_status(task.get("state")),
                    "priority": self._map_priority(task.get("priority")),
                    "origin": self.config.name,
                    "customer": task.get("customer") or company_name,
                    "group": task.get("group", "Support"),
                    "owner": owner_name,
                    "owner_email": owner_email,
                    "created_at": self._format_date(task.get("created_at")),
                    "updated_at": self._format_date(task.get("updated_at")),
                    "due_date": self._format_date(task.get("escalation_at")),
                    "url": f"{self.base_url}/#ticket/zoom/{task.get('id')}",
                    "extra_info": {
                        "group_id": task.get("group_id"),
                        "organization_id": task.get("organization_id"),
                    },
                },
            )
        return normalized_tasks

    def _map_status(self, zammad_state):
        """Map Zammad specific states to our Dashboard states
        (open, pending, resolved)"""
        # Note: Depending on your Zammad setup, 'state' might be an ID or a
        # Dict if expanded. This handles the text representation.
        if isinstance(zammad_state, dict):
            state_name = zammad_state.get("name", "").lower()
        else:
            state_name = str(zammad_state).lower()

        if state_name in ["new", "open"]:
            return "open"
        if state_name in ["closed", "merged"]:
            return "closed"
        return "pending"

    def _map_priority(self, zammad_priority):
        if isinstance(zammad_priority, dict):
            prio = zammad_priority.get("name", "").lower()
        else:
            prio = str(zammad_priority).lower()

        if "3" in prio or "high" in prio:
            return "High"
        if "4" in prio or "urgent" in prio:
            return "Critical"
        if "1" in prio or "low" in prio:
            return "Low"
        return "Medium"

    def _format_date(self, date_str):
        """Returns the full ISO string for proper duration calculation."""
        if not date_str:
            return ""
        try:
            # Zammad returns ISO 8601, ensure it has offset for fromisoformat
            dt_str = date_str.replace("Z", "+00:00")
            # Validate it's a valid ISO string
            datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return date_str
        else:
            return dt_str

    def check_health(self):
        start = django_timezone.now()

        if not self.base_url or not self.token:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing URL or Token in configuration",
            }

        try:
            response = httpx.get(
                f"{self.base_url}/api/v1/users/me",
                headers=self.headers,
                timeout=10.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            logger.warning("%s Auth Failed: %s", self.config.name, e)
            return {
                "name": self.config.name,
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception:
            logger.exception("%s Unreachable", self.config.name)
            return {
                "name": self.config.name,
                "status": "offline",
                "latency": 0,
                "error": "Unreachable",
            }
        else:
            latency = int((django_timezone.now() - start).total_seconds() * 1000)
            return {
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }
