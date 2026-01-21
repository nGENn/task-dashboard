import logging
from http import HTTPStatus

import requests
from django.core.cache import cache
from django.utils import timezone as django_timezone
from requests import RequestException

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

    def _get_user_map(self):
        """Map Zammad User ID -> Email"""
        cache_key = f"zammad_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v1/users"
            # Zammad users API is paginated, but for now we'll fetch first 250
            # which usually covers the agents/owners.
            resp = requests.get(
                url,
                headers=self.headers,
                params={"per_page": 250},
                timeout=10,
            )
            if resp.status_code == HTTPStatus.OK:
                for u in resp.json():
                    uid = u.get("id")
                    email = u.get("email")
                    name = (
                        f"{u.get('firstname', '')} "
                        f"{u.get('lastname', '')}"
                    ).strip() or u.get("login")
                    if uid:
                        user_map[uid] = {"email": email, "name": name}

            cache.set(cache_key, user_map, timeout=3600)
        except RequestException as e:
            logger.warning("Zammad User Map failed: %s", e)
        return user_map

    def get_tickets(self, *, force_refresh=False):
        cache_key = f"zammad_{self.config.id}_active_tickets_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.debug("Returning cached Zammad data")
                return cached_data

        if not self.base_url or not self.token:
            logger.warning("Zammad credentials not found.")
            return []

        user_map = self._get_user_map()

        try:
            raw_tickets = self._fetch_all_tickets()
            normalized_tickets = self._normalize_tickets(raw_tickets, user_map)

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets  # noqa: TRY300

        except RequestException:
            logger.exception("Error fetching Zammad tickets")
            return []
        except Exception:
            logger.exception("Unexpected error fetching Zammad tickets")
            raise

    def _fetch_all_tickets(self):
        url = f"{self.base_url}/api/v1/tickets"
        raw_tickets = []
        page = 1
        per_page = 100
        max_pages = 100

        logger.debug("Fetching Zammad tickets (paginated): %s", url)

        while page <= max_pages:
            params = {
                "expand": "true",
                "page": page,
                "per_page": per_page,
                "order_by": "updated_at",
                "sort_by": "desc",
            }

            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            if isinstance(data, dict):
                page_tickets = data.get("tickets", [])
            else:
                page_tickets = data

            if not page_tickets:
                break

            raw_tickets.extend(page_tickets)
            logger.debug("Page %d: Found %d tickets.", page, len(page_tickets))

            if len(page_tickets) < per_page:
                break

            page += 1

        if page > max_pages:
            logger.warning(
                "Zammad fetch limit reached (%d tickets).", len(raw_tickets)
            )
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
            return "resolved"
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
            from datetime import datetime
            datetime.fromisoformat(dt_str)
            return dt_str
        except (ValueError, TypeError):
            return date_str

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
            response = requests.get(
                f"{self.base_url}/api/v1/users/me",
                headers=self.headers,
                timeout=3,
            )
            response.raise_for_status()

            latency = int(
                (django_timezone.now() - start).total_seconds() * 1000,
            )
            return {  # noqa: TRY300
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }
        except requests.HTTPError as e:
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
