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
        # (Keep existing health check)
        start = datetime.now()
        if not self.api_key:
            return {
                "name": "EspoCRM",
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing API Key",
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
            return {
                "name": "EspoCRM",
                "status": "auth_error",
                "latency": 0,
                "error": str(e),
            }
        except Exception as e:
            return {
                "name": "EspoCRM",
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }

    def _get_user_map(self):
        """
        Fetch all users to map ID -> EmailAddress.
        """
        # CACHE DISABLED FOR DEBUGGING
        # cache_key = "espo_user_map"
        # ...

        user_map = {}
        try:
            print("DEBUG ESPO: Fetching User Map...", flush=True)
            # Explicitly select emailAddress
            url = f"{self.base_url}/api/v1/User"
            params = {"maxSize": 200, "select": "id,emailAddress,userName"}

            resp = requests.get(url, headers=self.headers, params=params, timeout=5)

            if resp.status_code != 200:
                print(
                    f"DEBUG ESPO: User Map Failed {resp.status_code}: {resp.text}",
                    flush=True,
                )
                return {}

            users = resp.json().get("list", [])
            print(f"DEBUG ESPO: Found {len(users)} users in directory.", flush=True)

            for u in users:
                uid = u.get("id")
                email = u.get("emailAddress")

                print(
                    f"DEBUG ESPO: Map User {u.get('id')} -> {u.get('userName')} ({u.get('emailAddress')})",
                    flush=True,
                )

                if uid:
                    # Fallback to username if email is missing (better than nothing)
                    user_map[uid] = (
                        email if email else f"{u.get('userName')}@placeholder"
                    )
                    if not email:
                        print(
                            f"DEBUG ESPO: User {u.get('userName')} has NO EMAIL. Using placeholder.",
                            flush=True,
                        )

            print(f"DEBUG ESPO: Map built with {len(user_map)} entries.", flush=True)
            # cache.set(cache_key, user_map, timeout=3600)
        except Exception as e:
            print(f"DEBUG ESPO: Map Exception: {e}", flush=True)
            logger.warning(f"Espo User Map failed: {e}")

        return user_map

    def get_tickets(self, force_refresh=False):
        cache_key = "espo_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        user_map = self._get_user_map()
        normalized_tickets = []

        try:
            print("DEBUG ESPO: Starting fetch...", flush=True)

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

            print(
                f"DEBUG ESPO: Total items found: {len(normalized_tickets)}", flush=True
            )

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets

        except Exception as e:
            logger.error(f"Error fetching EspoCRM data: {e}")
            print(f"DEBUG ESPO: Error {e}", flush=True)
            return []

    def _fetch_entity(self, url, entity_type, params, target_list, user_map):
        try:
            resp = requests.get(url, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("list", [])
            print(f"DEBUG ESPO: Fetched {len(items)} {entity_type}s", flush=True)

            for item in items:
                owner_id = item.get("assignedUserId")
                owner_email = user_map.get(owner_id)

                target_list.append(
                    {
                        "id": f"ESPO-{entity_type[0]}-{item.get('number')}",
                        "title": item.get("name"),
                        "status": self._map_status(item.get("status")),
                        "priority": item.get("priority", "Medium"),
                        "origin": "EspoCRM",
                        "customer": item.get("accountName", "Unknown"),
                        "group": entity_type,
                        "owner": item.get("assignedUserName", "Unassigned"),
                        "owner_email": owner_email,
                        "created_at": str(item.get("createdAt", "")).split(" ")[0],
                        "updated_at": str(item.get("modifiedAt", "")).split(" ")[0],
                        "url": f"{self.base_url}/#{entity_type}/view/{item.get('id')}",
                    }
                )
        except Exception as e:
            print(f"DEBUG ESPO: Failed to fetch {entity_type}: {e}", flush=True)

    def _map_status(self, espo_status):
        s = str(espo_status).lower()
        if s in ["new", "assigned", "pending", "not started", "in progress"]:
            return "open"
        if s in ["closed", "rejected", "merged", "completed"]:
            return "resolved"
        return "pending"
