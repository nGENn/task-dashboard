import asyncio
import logging
from datetime import datetime
from http import HTTPStatus

import httpx
from django.core.cache import cache
from django.utils import timezone as django_timezone

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

    def get_tickets(self, *, force_refresh=False):
        return asyncio.run(self.get_tickets_async(force_refresh=force_refresh))

    async def get_tickets_async(self, *, force_refresh=False):
        cache_key = f"zammad_{self.config.id}_active_tickets_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.debug("Returning cached Zammad data")
                return cached_data

        if not self.base_url or not self.token:
            logger.warning("Zammad credentials not found.")
            return []

        async with httpx.AsyncClient() as client:
            user_map = await self._get_user_map(client)

            try:
                raw_tickets = await self._fetch_all_tickets_async(client)
                normalized_tickets = self._normalize_tickets(raw_tickets, user_map)
            except httpx.HTTPError:
                logger.exception("Error fetching Zammad tasks")
                # Instead of returning [], we raise so fetch_service_tickets knows it
                # failed (PLR0913 workaround)
                raise
            except Exception:
                logger.exception("Unexpected error fetching Zammad tasks")
                raise
            else:
                cache.set(cache_key, normalized_tickets, timeout=300)
                return normalized_tickets

    async def _fetch_all_tickets_async(self, client: httpx.AsyncClient):
        url = f"{self.base_url}/api/v1/tickets"
        raw_tickets = []
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
        first_page_tickets = data.get("tickets", []) if isinstance(data, dict) else data
        if not first_page_tickets:
            return []

        raw_tickets.extend(first_page_tickets)

        # If we have a full first page, fetch subsequent pages.
        # SAFEGUARD: Use a Semaphore to limit concurrency and avoid overwhelming Zammad
        if len(first_page_tickets) == per_page:
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
                    raw_tickets.extend(res)
                elif isinstance(res, Exception):
                    logger.warning("Failed to fetch Zammad page: %s", res)
                    # We continue even if one page fails, as we have other data

        return raw_tickets

    def _normalize_tickets(self, raw_tickets, user_map):
        normalized_tickets = []
        for ticket in raw_tickets:
            owner_id = ticket.get("owner_id")
            user_info = user_map.get(owner_id, {})
            owner_name = user_info.get("name", "Unassigned")
            owner_email = user_info.get("email")

            normalized_tickets.append(
                {
                    "id": f"ZAM-{ticket.get('number')}",
                    "title": ticket.get("title"),
                    "status": self._map_status(ticket.get("state")),
                    "priority": self._map_priority(ticket.get("priority")),
                    "origin": self.config.name,
                    "customer": ticket.get("customer", "Unknown"),
                    "group": ticket.get("group", "Support"),
                    "owner": owner_name,
                    "owner_email": owner_email,
                    "created_at": self._format_date(ticket.get("created_at")),
                    "updated_at": self._format_date(ticket.get("updated_at")),
                    "due_date": self._format_date(ticket.get("escalation_at")),
                    "url": f"{self.base_url}/#ticket/zoom/{ticket.get('id')}",
                    "extra_info": {
                        "group_id": ticket.get("group_id"),
                        "organization_id": ticket.get("organization_id"),
                    },
                },
            )
        return normalized_tickets

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
