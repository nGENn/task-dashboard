import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


class EspoService:
    def __init__(self):
        self.base_url = getattr(settings, "ESPO_API_URL", "")
        self.api_key = getattr(settings, "ESPO_API_KEY", "")
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def check_health(self):
        start = datetime.now()

        if not self.api_key:
            return {
                "name": "EspoCRM",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key in settings",
            }

        try:
            response = requests.get(
                f"{self.base_url}/api/v1/App/user", headers=self.headers, timeout=3
            )
            response.raise_for_status()

            latency = int((datetime.now() - start).total_seconds() * 1000)
            return {
                "name": "EspoCRM",
                "status": "online",
                "latency": latency,
                "error": None,
            }

        except requests.HTTPError as e:
            logger.warning(f"EspoCRM Auth Failed: {e}")
            return {
                "name": "EspoCRM",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception as e:
            logger.error(f"EspoCRM Unreachable: {e}")
            return {
                "name": "EspoCRM",
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }

    def get_tickets(self, force_refresh=False):
        """
        Fetches Active Cases AND Tasks from EspoCRM.
        """
        cache_key = "espo_active_items_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        normalized_tickets = []

        try:
            # Shared params for both endpoints
            # Status: Not Closed, Merged, or Canceled
            params = {
                "maxSize": 100,
                "where[0][type]": "notIn",
                "where[0][attribute]": "status",
                "where[0][value][]": [
                    "Closed",
                    "Merged",
                    "Declined",
                    "Canceled",
                    "Completed",
                ],
            }

            # 1. FETCH CASES
            self._fetch_entity(
                f"{self.base_url}/api/v1/Case", "Case", params, normalized_tickets
            )

            # 2. FETCH TASKS (Manager Request)
            self._fetch_entity(
                f"{self.base_url}/api/v1/Task", "Task", params, normalized_tickets
            )

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets

        except Exception as e:
            logger.error(f"Error fetching EspoCRM data: {e}")
            return []

    def _fetch_entity(self, url, entity_type, params, target_list):
        """Helper to fetch different Espo entities"""
        try:
            response = requests.get(
                url, headers=self.headers, params=params, timeout=10
            )
            response.raise_for_status()

            data = response.json()
            raw_list = data.get("list", [])

            for item in raw_list:
                # ID Prefix: ESPO-C (Case) vs ESPO-T (Task)
                prefix = "ESPO-T" if entity_type == "Task" else "ESPO-C"

                target_list.append(
                    {
                        "id": f"{prefix}-{item.get('number')}",
                        "title": item.get("name"),
                        "status": self._map_status(item.get("status")),
                        "priority": item.get("priority", "Medium"),
                        "origin": "EspoCRM",
                        "customer": item.get(
                            "accountName", "Internal"
                        ),  # Tasks often internal
                        "group": entity_type,  # Group by "Case" or "Task"
                        "owner": item.get("assignedUserName", "Unassigned"),
                        "created_at": str(item.get("createdAt", "")).split(" ")[0],
                        "updated_at": str(item.get("modifiedAt", "")).split(" ")[0],
                        "url": f"{self.base_url}/#{entity_type}/view/{item.get('id')}",
                    }
                )
        except Exception as e:
            logger.warning(f"Failed to fetch Espo {entity_type}: {e}")

    def _map_status(self, espo_status):
        s = str(espo_status).lower()
        if s in ["new", "assigned"]:
            return "open"
        if s in ["closed", "rejected"]:
            return "resolved"
        return "pending"  # 'Pending Input', 'Processing', etc.
