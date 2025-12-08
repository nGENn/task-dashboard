import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class ZammadService:
    def __init__(self):
        self.base_url = getattr(settings, "ZAMMAD_API_URL", "")
        self.token = getattr(settings, "ZAMMAD_API_TOKEN", "")

        self.headers = {
            "Authorization": f"Token token={self.token}",
            "Content-Type": "application/json",
        }

    def get_tickets(self, force_refresh=False):
        # 1. Define Cache Key
        cache_key = "zammad_active_tickets_cache"

        # 2. Return Cache if available
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                # IMPORTANT: Print here to confirm cache usage
                print("DEBUG: Returning cached Zammad data", flush=True)
                return cached_data

        # --- DEBUGGING START (Added flush=True) ---
        print(f"DEBUG: Checking Zammad Credentials...", flush=True)
        print(f"DEBUG: URL: '{self.base_url}'", flush=True)
        # Check if token exists (don't print the whole thing)
        has_token = "YES" if self.token else "NO"
        print(f"DEBUG: Has Token: {has_token}", flush=True)
        # --- DEBUGGING END ---

        if not self.base_url or not self.token:
            # Use logger.warning as well, which usually flushes automatically
            logger.warning("Zammad credentials not found.")
            print(
                "DEBUG: Credentials missing in settings! Returning empty list.",
                flush=True,
            )
            return []

        try:
            # 3. Build Query: Fetch EVERYTHING active (New, Open, Pending)
            url = f"{self.base_url}/api/v1/tickets?expand=true&limit=50&order_by=updated_at&sort_by=desc"

            print(f"DEBUG: Attempting to fetch: {url}")

            response = requests.get(url, headers=self.headers, timeout=10)

            # This will trigger the exception if status is 401, 404, 500 etc.
            response.raise_for_status()

            # Zammad Search returns a dict with 'tickets', 'users', etc.
            data = response.json()
            # Handle potential difference between List/Search response structures
            raw_tickets = data.get("tickets", []) if isinstance(data, dict) else data

            print(f"DEBUG: Fetch successful. Found {len(raw_tickets)} tickets.")

            normalized_tickets = []

            for ticket in raw_tickets:
                normalized_tickets.append(
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
                )

            # 4. Save to Cache (5 Minutes = 300 seconds)
            cache.set(cache_key, normalized_tickets, timeout=300)

            return normalized_tickets

        except Exception as e:
            # CRITICAL: Print and Raise so you see the yellow error page
            print(f"DEBUG: Zammad Request Failed: {e}")
            if "response" in locals():
                print(f"DEBUG: Response Content: {response.text}")
            logger.error(f"Error fetching Zammad tickets: {e}")
            raise e

        except requests.RequestException as e:
            logger.error(f"Error fetching Zammad tickets: {e}")
            return []

    def _map_status(self, zammad_state):
        """Map Zammad specific states to our Dashboard states (open, pending, resolved)"""
        # Note: Depending on your Zammad setup, 'state' might be an ID or a Dict if expanded
        # This handles the text representation
        if isinstance(zammad_state, dict):
            state_name = zammad_state.get("name", "").lower()
        else:
            state_name = str(zammad_state).lower()

        if state_name in ["new", "open"]:
            return "open"
        elif state_name in ["closed", "merged"]:
            return "resolved"
        else:
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
        start = datetime.now()

        if not self.base_url or not self.token:
            return {
                "name": "Zammad",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing URL or Token in settings",
            }

        try:
            response = requests.get(
                f"{self.base_url}/api/v1/users/me", headers=self.headers, timeout=3
            )
            response.raise_for_status()

            latency = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "name": "Zammad",
                "status": "online",
                "latency": latency,
                "error": None,
            }

        except requests.HTTPError as e:
            logger.warning(f"Zammad Auth Failed: {e}")
            return {
                "name": "Zammad",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"Zammad Unreachable: {e}")
            return {
                "name": "Zammad",
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }
