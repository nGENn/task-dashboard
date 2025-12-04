import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class OpenProjectService:
    def __init__(self):
        self.base_url = getattr(settings, "OPENPROJECT_API_URL", "")
        self.api_key = getattr(settings, "OPENPROJECT_API_KEY", "")
        # OpenProject uses Basic Auth: user="apikey", password=YOUR_TOKEN
        self.auth = HTTPBasicAuth("apikey", self.api_key)

    def check_health(self):
        start = datetime.now()

        if not self.api_key:
            return {
                "name": "OpenProject",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key",
            }

        try:
            # Simple ping: Get current user
            response = requests.get(
                f"{self.base_url}/api/v3/users/me", auth=self.auth, timeout=3
            )
            response.raise_for_status()

            latency = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "name": "OpenProject",
                "status": "online",
                "latency": latency,
                "error": None,
            }

        except requests.HTTPError as e:
            logger.warning(f"OpenProject Auth Failed: {e}")
            return {
                "name": "OpenProject",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"OpenProject Unreachable: {e}")
            return {
                "name": "OpenProject",
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }

    def get_tickets(self, force_refresh=False):
        cache_key = "openproject_active_packages_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        try:
            # Fetch Work Packages
            # We filter for non-closed statuses manually or fetch recent ones.
            # OpenProject V3 filter syntax in URL is complex, so we fetch recent items
            # and filter 'closed' logic in Python for simplicity, or rely on sorting.
            url = f"{self.base_url}/api/v3/work_packages"
            params = {
                "pageSize": 100,
                "sortBy": '[["updatedAt","desc"]]',
            }

            response = requests.get(url, auth=self.auth, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()
            embedded = data.get("_embedded", {})
            elements = embedded.get("elements", [])

            normalized_tickets = []

            for item in elements:
                # Extract embedded fields (OpenProject puts data in weird places sometimes)
                # We assume standard HAL+JSON format

                # Status Logic (OpenProject usually has status types like "Closed", "Rejected")
                status_text = (
                    item.get("_links", {}).get("status", {}).get("title", "Unknown")
                )
                mapped_status = self._map_status(status_text)

                # Skip closed tickets if we are fetching "active" ones
                if mapped_status == "resolved":
                    continue

                normalized_tickets.append(
                    {
                        "id": f"OP-{item.get('id')}",
                        "title": item.get("subject"),
                        "status": mapped_status,
                        "priority": item.get("_links", {})
                        .get("priority", {})
                        .get("title", "Medium"),
                        "origin": "OpenProject",
                        "customer": item.get("_links", {})
                        .get("project", {})
                        .get("title", "Project"),  # Mapping Project -> Customer
                        "group": "Project",
                        "owner": item.get("_links", {})
                        .get("assignee", {})
                        .get("title", "Unassigned"),
                        "created_at": self._format_date(item.get("createdAt")),
                        "updated_at": self._format_date(item.get("updatedAt")),
                        "url": f"{self.base_url}/work_packages/{item.get('id')}",
                    }
                )

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets

        except Exception as e:
            logger.error(f"Error fetching OpenProject packages: {e}")
            return []

    def _map_status(self, status_text):
        s = str(status_text).lower()
        if s in ["new", "in progress", "scheduled", "open"]:
            return "open"
        if s in ["closed", "rejected", "completed"]:
            return "resolved"
        return "pending"  # 'On hold', 'Testing', etc.

    def _format_date(self, date_str):
        if not date_str:
            return ""
        try:
            # ISO 8601
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return date_str
