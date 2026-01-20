import logging
from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import requests
from django.conf import settings
from django.core.cache import cache
from requests import RequestException

logger = logging.getLogger(__name__)


class EspoService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.api_key = config.api_token
        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    def check_health(self):
        start = datetime.now(tz=UTC)
        if not self.api_key:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key",
            }
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/App/user",
                headers=self.headers,
                timeout=3,
            )
            response.raise_for_status()
            latency = int((datetime.now(tz=UTC) - start).total_seconds() * 1000)
        except requests.HTTPError as e:
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
            return {
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }

    def _get_user_map(self):
        """
        Fetch all users to map ID -> EmailAddress.
        """
        cache_key = f"espo_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            # Explicitly select emailAddress
            url = f"{self.base_url}/api/v1/User"
            params = {"maxSize": 200, "select": "id,emailAddress,userName"}

            resp = requests.get(url, headers=self.headers, params=params, timeout=5)

            if resp.status_code == HTTPStatus.OK:
                users = resp.json().get("list", [])

                for u in users:
                    uid = u.get("id")
                    email = u.get("emailAddress")

                    if uid:
                        # Fallback to username if email is missing (better than nothing)
                        user_map[uid] = (
                            email if email else f"{u.get('userName')}@placeholder"
                        )

            cache.set(cache_key, user_map, timeout=3600)
        except RequestException as e:
            logger.warning("Espo User Map failed: %s", e)

        return user_map

    def get_tickets(self, *, force_refresh=False):
        cache_key = f"espo_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        user_map = self._get_user_map()
        normalized_tickets = []

        try:
            # 1. Fetch Cases
            # Use minimal params to ensure we get data
            params = {"maxSize": 50, "orderBy": "createdAt", "order": "desc"}

            # Fetch Cases
            self._fetch_entity(
                f"{self.base_url}/api/v1/Case",
                "Case",
                params,
                normalized_tickets,
                user_map,
            )

            # Fetch Tasks
            self._fetch_entity(
                f"{self.base_url}/api/v1/Task",
                "Task",
                params,
                normalized_tickets,
                user_map,
            )

            cache.set(cache_key, normalized_tickets, timeout=300)

        except RequestException:
            logger.exception("Error fetching EspoCRM data")
            return []
        else:
            return normalized_tickets

    def _fetch_entity(self, url, entity_type, params, target_list, user_map):
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("list", [])

            for item in items:
                owner_id = item.get("assignedUserId")
                owner_email = user_map.get(owner_id)

                target_list.append(
                    {
                        "id": f"ESPO-{entity_type[0]}-{item.get('number')}",
                        "title": item.get("name"),
                        "status": self._map_status(item.get("status")),
                        "priority": item.get("priority", "Medium"),
                        "origin": self.config.name,
                        "customer": item.get("accountName", "Unknown"),
                        "group": entity_type,
                        "owner": item.get("assignedUserName", "-"),
                        "owner_email": owner_email,
                        "created_at": str(item.get("createdAt", "")).split(" ")[0],
                        "updated_at": str(item.get("modifiedAt", "")).split(" ")[0],
                        "url": f"{self.base_url}/#{entity_type}/view/{item.get('id')}",
                    },
                )
        except RequestException as e:
            logger.warning("Failed to fetch Espo %s: %s", entity_type, e)

    def _map_status(self, espo_status):
        s = str(espo_status).lower()
        if s in ["new", "assigned", "pending", "not started", "in progress"]:
            return "open"
        if s in ["closed", "rejected", "merged", "completed"]:
            return "resolved"
        return "pending"
