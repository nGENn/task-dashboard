import logging
from datetime import UTC
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from requests import RequestException

logger = logging.getLogger(__name__)


class ZammadService:
    def __init__(self):
        self.base_url = getattr(settings, "ZAMMAD_API_URL", "")
        self.token = getattr(settings, "ZAMMAD_API_TOKEN", "")

        self.headers = {
            "Authorization": f"Token token={self.token}",
            "Content-Type": "application/json",
        }

    def get_tickets(self, *, force_refresh=False):
        # 1. Define Cache Key
        cache_key = "zammad_active_tickets_cache"

        # 2. Return Cache if available
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                logger.debug("Returning cached Zammad data")
                return cached_data

        # --- DEBUGGING START (Added flush=True) ---
        logger.debug("Checking Zammad credentials")
        logger.debug("URL: %s", self.base_url)
        # Check if token exists (don't print the whole thing)
        has_token = "YES" if self.token else "NO"
        logger.debug("Has Token: %s", has_token)
        # --- DEBUGGING END ---

        if not self.base_url or not self.token:
            # Use logger.warning as well, which usually flushes automatically
            logger.warning("Zammad credentials not found.")
            logger.debug("Credentials missing in settings; returning empty list")
            return []

        try:
            # 3. Build Query: Fetch active tickets
            url = f"{self.base_url}/api/v1/tickets"
            params = {
                "expand": "true",
                "limit": 50,
                "order_by": "updated_at",
                "sort_by": "desc",
            }

            logger.debug("Attempting to fetch Zammad tickets: %s", url)

            response = requests.get(
                url,
                headers=self.headers,
                params=params,
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            raw_tickets = data.get("tickets", []) if isinstance(data, dict) else data

            logger.debug("Fetch successful. Found %d tickets.", len(raw_tickets))

            normalized_tickets = [
                {
                    "id": f"ZAM-{ticket.get('number')}",
                    "title": ticket.get("title"),
                    "status": self._map_status(ticket.get("state")),
                    "priority": self._map_priority(ticket.get("priority")),
                    "origin": "Zammad",
                    "customer": ticket.get("customer", "Unknown"),
                    "group": ticket.get("group", "Support"),
                    "owner": ticket.get("owner", "Unassigned"),
                    "created_at": self._format_date(ticket.get("created_at")),
                    "updated_at": self._format_date(ticket.get("updated_at")),
                    "url": f"{self.base_url}/#ticket/zoom/{ticket.get('id')}",
                }
                for ticket in raw_tickets
            ]

            # 4. Save to Cache (5 Minutes = 300 seconds)
            cache.set(cache_key, normalized_tickets, timeout=300)

            return normalized_tickets  # noqa: TRY300

        except RequestException:
            # Log request errors and return empty list
            logger.exception("Error fetching Zammad tickets")
            return []

        except Exception:
            # Unexpected errors: log full traceback and re-raise
            logger.exception("Unexpected error fetching Zammad tickets")
            raise

    def _map_status(self, zammad_state):
        """Map Zammad specific states to our Dashboard states
        (open, pending, resolved)"""
        # Note: Depending on your Zammad setup, 'state' might be an ID or a Dict if expanded  # noqa: E501
        # This handles the text representation
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
        """Convert ISO string to YYYY-MM-DD for proper sorting/filtering"""
        if not date_str:
            return ""
        try:
            # Zammad returns ISO 8601
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")  # Changed from "%b %d, %Y"
        except ValueError:
            return date_str

    def check_health(self):
        start = datetime.now(tz=UTC)

        if not self.base_url or not self.token:
            return {
                "name": "Zammad",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing URL or Token in settings",
            }

        try:
            response = requests.get(
                f"{self.base_url}/api/v1/users/me",
                headers=self.headers,
                timeout=3,
            )
            response.raise_for_status()

            latency = int(
                (datetime.now(tz=UTC) - start).total_seconds() * 1000,
            )
            return {  # noqa: TRY300
                "name": "Zammad",
                "status": "online",
                "latency": latency,
                "error": None,
            }
        except requests.HTTPError as e:
            logger.warning("Zammad Auth Failed: %s", e)
            return {
                "name": "Zammad",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception:
            logger.exception("Zammad Unreachable")
            return {
                "name": "Zammad",
                "status": "offline",
                "latency": 0,
                "error": "Unreachable",
            }
