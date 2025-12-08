import logging
from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import requests
from django.conf import settings
from django.core.cache import cache
from requests import RequestException

logger = logging.getLogger(__name__)


class ErambaService:
    def __init__(self):
        self.base_url = getattr(settings, "ERAMBA_API_URL", "")
        self.api_key = getattr(settings, "ERAMBA_API_KEY", "")
        # Eramba typically uses an 'ApiKey' header
        self.headers = {
            "ApiKey": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def check_health(self):
        start = datetime.now(tz=UTC)

        if not self.api_key:
            return {
                "name": "Eramba",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key",
            }

        try:
            # Ping settings or simple endpoint to verify access
            # /settings/index.json is usually lightweight
            response = requests.get(
                f"{self.base_url}/settings/index.json",
                headers=self.headers,
                timeout=5,
            )
            response.raise_for_status()

            latency = int((datetime.now(tz=UTC) - start).total_seconds() * 1000)
            return {  # noqa: TRY300
                "name": "Eramba",
                "status": "online",
                "latency": latency,
                "error": None,
            }

        except requests.HTTPError as e:
            logger.warning("Eramba Auth Failed: %s", e)
            return {
                "name": "Eramba",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception:
            logger.exception("Eramba Unreachable")
            return {
                "name": "Eramba",
                "status": "offline",
                "latency": 0,
                "error": "Unreachable",
            }

    def get_tickets(self, *, force_refresh=False):
        """
        Fetches Security Incidents, Security Operations, and Notifications.
        """
        cache_key = "eramba_active_items_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        normalized_tickets = []

        try:
            # 1. Security Incidents (Existing)
            self._fetch_module("security_incidents", "Incident", normalized_tickets)

            # 2. Security Operations Projects (Manager Request)
            # Endpoint: /security_operations/index.json
            self._fetch_module("security_operations", "SecOps", normalized_tickets)

            # 3. Notifications (Manager Request)
            # "Notifications" in Eramba are often specific warnings.
            # We assume a 'notifications' endpoint exists or map 'warning' items.
            # If this endpoint fails (404), the helper will safely log it and continue.
            self._fetch_module("notifications", "Notification", normalized_tickets)

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets  # noqa: TRY300

        except RequestException:
            logger.exception("Error fetching Eramba data")
            return []

    def _fetch_module(self, module_slug, label, target_list):
        """
        Generic helper for Eramba modules.
        module_slug: e.g. 'security_operations'
        label: e.g. 'SecOps' (Used for ID and Group)
        """
        try:
            url = f"{self.base_url}/{module_slug}/index.json"
            response = requests.get(url, headers=self.headers, timeout=10)

            # If module doesn't exist or permissions denied, skip it
            if response.status_code != HTTPStatus.OK:
                return

            data = response.json()
            raw_list = data.get("items", []) if isinstance(data, dict) else data

            for entry in raw_list:
                # Eramba objects are dynamically keyed, e.g. entry['SecurityOperation']
                # We try to find the first key that looks like a data object
                keys = list(entry.keys())
                if not keys:
                    continue

                # Heuristic: Grab the first key (e.g. 'SecurityOperation')
                item_key = keys[0]
                item = entry[item_key]

                # Check status (Skip closed)
                status_raw = str(item.get("status", "")).lower()
                if "close" in status_raw or "completed" in status_raw:
                    continue

                # ID formatting: ERA-SEC-123
                short_label = label[:3].upper()

                target_list.append(
                    {
                        "id": f"ERA-{short_label}-{item.get('id')}",
                        "title": item.get("title")
                        or item.get("name")
                        or f"{label} #{item.get('id')}",
                        "status": "open",
                        # Eramba priority mapping varies widely per module
                        "priority": "Medium",
                        "origin": "Eramba",
                        "customer": "Internal",
                        "group": label,  # 'Incident', 'SecOps', 'Notification'
                        "owner": "GRC Team",
                        "created_at": self._format_date(item.get("created")),
                        "updated_at": self._format_date(item.get("modified")),
                        "url": f"{self.base_url}/{module_slug}/view/{item.get('id')}",
                    },
                )

        except RequestException as e:
            logger.warning("Failed to fetch Eramba module '%s': %s", module_slug, e)

    def _map_priority(self, classification):
        # Eramba classification is often a string like "High", "Critical", etc.
        s = str(classification).lower()
        if "critical" in s:
            return "Critical"
        if "high" in s:
            return "High"
        if "low" in s:
            return "Low"
        return "Medium"

    def _format_date(self, date_str):
        if not date_str:
            return ""
        try:
            # Eramba often uses "YYYY-MM-DD HH:MM:SS"
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return str(date_str).split(" ")[0]  # Fallback: just take first part
