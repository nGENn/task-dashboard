import asyncio
import logging
from datetime import datetime
from http import HTTPStatus

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


class GitLabService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url or "https://gitlab.com"
        self.token = config.api_token
        self.headers = {"Private-Token": self.token}

    def get_tickets(self, *, force_refresh=False):
        return asyncio.run(self.get_tickets_async(force_refresh=force_refresh))

    async def get_tickets_async(self, *, force_refresh=False):
        cache_key = f"gitlab_{self.config.id}_active_items_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.token:
            logger.warning("GitLab credentials missing.")
            return []

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)

            normalized_items = []
            try:
                # Fetch Issues and MRs in parallel
                issues_url = f"{self.base_url}/api/v4/issues"
                issues_params = {
                    "scope": "all",
                    "state": "opened",
                    "order_by": "updated_at",
                }

                mrs_url = f"{self.base_url}/api/v4/merge_requests"
                mrs_params = {
                    "scope": "all",
                    "state": "opened",
                    "order_by": "updated_at",
                }

                await asyncio.gather(
                    self._fetch_and_normalize(client, issues_url, "Issue", normalized_items, user_map, params=issues_params),
                    self._fetch_and_normalize(client, mrs_url, "Merge Request", normalized_items, user_map, params=mrs_params),
                )

                cache.set(cache_key, normalized_items, timeout=300)
                return normalized_items

            except httpx.HTTPError:
                logger.exception("Error fetching GitLab data")
                return []

    async def _get_user_map(self, client: httpx.AsyncClient):
        map_cache_key = f"gitlab_{self.config.id}_user_email_map"
        cached_map = cache.get(map_cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v4/users?per_page=100&active=true"
            response = await client.get(url, headers=self.headers, timeout=10.0)
            if response.status_code == HTTPStatus.OK:
                for u in response.json():
                    email = u.get("public_email") or u.get("email")
                    if email:
                        user_map[u["id"]] = email
            cache.set(map_cache_key, user_map, timeout=3600)
        except httpx.HTTPError as e:
            logger.warning("Failed to build GitLab user map: %s", e)
        return user_map

    async def _fetch_and_normalize(self, client, url, item_type, target_list, user_map, params=None):
        try:
            # For simplicity, we'll fetch first 2 pages in parallel if needed, 
            # but usually start with page 1.
            page = 1
            per_page = 100
            
            request_params = (params or {}).copy()
            request_params.update({"page": page, "per_page": per_page})

            response = await client.get(url, headers=self.headers, params=request_params, timeout=15.0)
            response.raise_for_status()
            data = response.json()

            if not data:
                return

            self._process_items(data, item_type, target_list, user_map)

            # If page 1 was full, fetch page 2 concurrently
            if len(data) == per_page:
                request_params["page"] = 2
                resp2 = await client.get(url, headers=self.headers, params=request_params, timeout=15.0)
                if resp2.status_code == HTTPStatus.OK:
                    self._process_items(resp2.json(), item_type, target_list, user_map)

        except httpx.HTTPError as e:
            logger.warning("Failed to fetch GitLab %s: %s", item_type, e)

    def _process_items(self, data, item_type, target_list, user_map):
        for item in data:
            prefix = "GL-MR" if item_type == "Merge Request" else "GL-I"
            title_prefix = "[MR] " if item_type == "Merge Request" else ""

            assignee_data = item.get("assignee")
            owner_name = "-"
            owner_email = None

            if assignee_data:
                owner_name = assignee_data.get("name")
                user_id = assignee_data.get("id")
                owner_email = user_map.get(user_id)

            full_ref = item.get("references", {}).get("full", "")
            group_name = full_ref.split("#")[0] if "#" in full_ref else "GitLab"

            target_list.append({
                "id": f"{prefix}-{item.get('iid')}",
                "title": f"{title_prefix}{item.get('title')}",
                "status": "open",
                "priority": self._extract_priority(item.get("labels", [])),
                "origin": self.config.name,
                "customer": group_name.split("/")[0] if "/" in group_name else group_name,
                "group": group_name,
                "owner": owner_name,
                "owner_email": owner_email,
                "created_at": self._format_date(item.get("created_at")),
                "updated_at": self._format_date(item.get("updated_at")),
                "due_date": self._format_date(item.get("due_date")),
                "url": item.get("web_url"),
                "extra_info": {
                    "project_id": item.get("project_id"),
                    "namespace": group_name.split("/")[0] if "/" in group_name else group_name,
                },
            })

    def _extract_priority(self, labels):
        labels = [lab.lower() for lab in labels]
        if any("critical" in lab or "urgent" in lab for lab in labels):
            return "Critical"
        if any("high" in lab for lab in labels):
            return "High"
        if any("low" in lab for lab in labels):
            return "Low"
        return "Medium"

    def _format_date(self, date_str):
        if not date_str:
            return ""
        try:
            dt_str = date_str.replace("Z", "+00:00")
            datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return date_str
        else:
            return dt_str

    def check_health(self):
        start = django_timezone.now()
        if not self.token:
            return {"name": self.config.name, "status": "auth_missing", "latency": 0, "error": "Missing Token"}

        try:
            response = httpx.get(f"{self.base_url}/api/v4/user", headers=self.headers, timeout=3.0)
            response.raise_for_status()
            latency = int((django_timezone.now() - start).total_seconds() * 1000)
            return {"name": self.config.name, "status": "online", "latency": latency, "error": None}
        except httpx.HTTPError as e:
            logger.warning("%s Auth Failed: %s", self.config.name, e)
            return {"name": self.config.name, "status": "auth_error", "latency": 0, "error": str(e)}
        except Exception:
            logger.exception("%s Unreachable", self.config.name)
            return {"name": self.config.name, "status": "offline", "latency": 0, "error": "Unreachable"}
