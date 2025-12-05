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
        self.auth = HTTPBasicAuth("apikey", self.api_key)
        self.host_header = getattr(settings, "OPENPROJECT_HOST_HEADER", None)

    def _get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.host_header:
            headers["Host"] = self.host_header
        return headers

    def check_health(self):
        # (Keep existing)
        return {"name": "OpenProject", "status": "online", "latency": 0}

    def _get_user_map(self):
        """Map OpenProject User ID -> Email"""
        # CACHE DISABLED FOR DEBUGGING
        # cache_key = "op_user_map" ...

        user_map = {}
        try:
            print("DEBUG OP: Fetching User Map...", flush=True)
            url = f"{self.base_url}/api/v3/users"
            resp = requests.get(
                url,
                auth=self.auth,
                headers=self._get_headers(),
                params={"pageSize": 100},
                timeout=10,
            )

            if resp.status_code == 200:
                elements = resp.json().get("_embedded", {}).get("elements", [])
                print(f"DEBUG OP: Found {len(elements)} users.", flush=True)

                for u in elements:
                    uid = u.get("id")
                    email = u.get("email")  # Often MISSING if not super-admin
                    login = u.get("login")

                    if uid:
                        # Fallback to login if email hidden
                        final_email = email if email else f"{login}@placeholder"
                        user_map[uid] = final_email

                        if not email:
                            print(
                                f"DEBUG OP: User {uid} ({u.get('name')}) hidden email. Using {final_email}",
                                flush=True,
                            )

            print(f"DEBUG OP: Map built with {len(user_map)} entries.", flush=True)
        except Exception as e:
            print(f"DEBUG OP: User Map Failed: {e}", flush=True)
            logger.warning(f"OpenProject User Map failed: {e}")
        return user_map

    def get_tickets(self, force_refresh=False):
        cache_key = "openproject_active_packages_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        user_map = self._get_user_map()
        normalized_tickets = []

        try:
            print("DEBUG OP: Starting fetch...", flush=True)
            url = f"{self.base_url}/api/v3/work_packages"
            params = {"pageSize": 50, "sortBy": '[["updatedAt","desc"]]'}

            response = requests.get(
                url,
                auth=self.auth,
                params=params,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()

            elements = response.json().get("_embedded", {}).get("elements", [])
            print(f"DEBUG OP: Fetched {len(elements)} work packages", flush=True)

            for item in elements:
                # ... (Keep existing parsing logic) ...
                links = item.get("_links", {})
                # ...

                # Extract Email Logic (Keep what we had)
                assignee_link = links.get("assignee", {})
                assignee_href = assignee_link.get("href", "")
                assignee_name = assignee_link.get("title", "Unassigned")
                assignee_email = None

                if assignee_href:
                    try:
                        uid = int(assignee_href.split("/")[-1])
                        assignee_email = user_map.get(uid)
                    except ValueError:
                        pass

                # Mapping Status
                status_title = links.get("status", {}).get("title", "Unknown")
                mapped_status = self._map_status(status_title)

                # REMOVED FILTER: if mapped_status == 'resolved': continue
                # We want to see EVERYTHING in debug mode

                normalized_tickets.append(
                    {
                        "id": f"OP-{item.get('id')}",
                        "title": item.get("subject"),
                        "status": mapped_status,
                        "priority": links.get("priority", {}).get("title", "Medium"),
                        "origin": "OpenProject",
                        "customer": links.get("project", {}).get("title", "Project"),
                        "group": "Project",
                        "owner": assignee_name,
                        "owner_email": assignee_email,
                        "created_at": str(item.get("createdAt", "")).split("T")[0],
                        "updated_at": str(item.get("updatedAt", "")).split("T")[0],
                        "url": f"{self.base_url}/work_packages/{item.get('id')}",
                    }
                )

            cache.set(cache_key, normalized_tickets, timeout=300)
            return normalized_tickets

        except Exception as e:
            print(f"DEBUG OP: Error {e}", flush=True)
            return []

    def _map_status(self, status_text):
        s = str(status_text).lower()
        if any(x in s for x in ["new", "open", "to do", "progress", "schedule"]):
            return "open"
        if any(x in s for x in ["closed", "done", "resolved", "reject"]):
            return "resolved"
        return "pending"
