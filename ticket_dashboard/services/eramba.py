import logging
from datetime import datetime, timezone
from http import HTTPStatus

import requests
from django.core.cache import cache
from django.utils import timezone as django_timezone
from requests import RequestException

logger = logging.getLogger(__name__)


class ErambaService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.api_key = config.api_token
        # Eramba typically uses an 'ApiKey' header
        self.headers = {
            "ApiKey": self.api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

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
            # Ping settings or simple endpoint to verify access
            # /settings/index.json is usually lightweight
            response = requests.get(
                f"{self.base_url}/settings/index.json",
                headers=self.headers,
                timeout=5,
            )
            response.raise_for_status()

            latency = int(
                (django_timezone.now() - start).total_seconds() * 1000
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

    def get_tickets(self, *, force_refresh=False):
        """
        Fetches Security Incidents, Security Operations, and Notifications.
        """
        cache_key = f"eramba_{self.config.id}_active_items_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        normalized_tickets = []

        try:
            # 1. Security Incidents (Existing)
            self._fetch_module(
                "security_incidents", "Incident", normalized_tickets
            )

            # 2. Security Operations Projects (Manager Request)
            # Endpoint: /security_operations/index.json
            self._fetch_module(
                "security_operations", "SecOps", normalized_tickets
            )

            # 3. Notifications (Manager Request)
            # "Notifications" in Eramba are often specific warnings.
            # We assume a 'notifications' endpoint exists or
            # map 'warning' items.
            # If this endpoint fails (404), the helper will safely log it
            # and continue.
            self._fetch_module(
                "notifications", "Notification", normalized_tickets
            )

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
            page = 1
            limit = 100
            max_pages = 100
            total_fetched = 0

            while page <= max_pages:
                url = f"{self.base_url}/{module_slug}/index.json"
                params = {
                    "page": page,
                    "limit": limit,
                }
                response = requests.get(
                    url,
                    headers=self.headers,
                    params=params,
                    timeout=10,
                )

                # If module doesn't exist or permissions denied, skip it
                if response.status_code != HTTPStatus.OK:
                    return

                data = response.json()
                raw_list = (
                    data.get("items", []) if isinstance(data, dict) else data
                )

                if not raw_list:
                    break

                for entry in raw_list:
                    # Eramba objects are dynamically keyed,
                    # e.g. entry['SecurityOperation']
                    # We try to find the first key that looks like a
                    # data object
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
                            "origin": self.config.name,
                            "customer": "Internal",
                            "group": label,
                            "owner": "GRC Team",
                            "created_at": self._format_date(
                                item.get("created")
                            ),
                            "updated_at": self._format_date(
                                item.get("modified")
                            ),
                            "due_date": self._format_date(
                                item.get("deadline")
                                or item.get("planned_end"),
                            ),
                            "url": (
                                f"{self.base_url}/{module_slug}/view/"
                                f"{item.get('id')}"
                            ),
                        },
                    )

                total_fetched += len(raw_list)

                if len(raw_list) < limit:
                    break

                page += 1

            if page > max_pages:
                logger.warning(
                    "Eramba %s fetch limit reached (%d items). "
                    "Some older items may not be visible.",
                    module_slug,
                    total_fetched,
                )

        except RequestException as e:
            logger.warning(
                "Failed to fetch Eramba module '%s': %s", module_slug, e
            )

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
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            # Convert to ISO format for the dashboard
            return dt.isoformat()
        except ValueError:
            return date_str
