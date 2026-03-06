import asyncio
import datetime
import logging
from http import HTTPStatus

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


class GitLabService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.token = config.api_token
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get_tickets(self, *, force_refresh=False):
        return asyncio.run(self.get_tickets_async(force_refresh=force_refresh))

    async def get_tickets_async(self, *, force_refresh=False):
        cache_key = f"gitlab_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.token:
            return []

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
                ctx = {"target": normalized_items, "user_map": user_map}
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

                ctx["target"].append(
                    {
                        "id": f"GL-{item_type[0]}-{item.get('iid')}",
                        "title": item.get("title"),
                        "status": "open",
                        "priority": "Medium",
                        "origin": self.config.name,
                        "customer": "Internal",
                        "group": item_type,
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
