import logging
from http import HTTPStatus

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone as django_timezone
from requests import RequestException
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)


class OpenProjectService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url
        self.api_key = config.api_token
        self.auth = HTTPBasicAuth("apikey", self.api_key)
        # Host header not yet in model, keeping as settings for now if needed,
        # but the model is the priority.
        self.host_header = getattr(settings, "OPENPROJECT_HOST_HEADER", None)

    def _get_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.host_header:
            headers["Host"] = self.host_header
        return headers

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
            requests.get(
                f"{self.base_url}/api/v3/users/me",
                auth=self.auth,
                headers=self._get_headers(),
                timeout=5,
            )
            latency = int(
                (django_timezone.now() - start).total_seconds() * 1000,
            )
        except RequestException as e:
            return {
                "name": self.config.name,
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }
        else:
            return {
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }

    def _get_user_map(self):
        """Map OpenProject User ID -> Email"""
        cache_key = f"op_{self.config.id}_user_map"
        cached_map = cache.get(cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            url = f"{self.base_url}/api/v3/users"
            resp = requests.get(
                url,
                auth=self.auth,
                headers=self._get_headers(),
                params={"pageSize": 100},
                timeout=10,
            )
            if resp.status_code == HTTPStatus.OK:
                elements = resp.json().get("_embedded", {}).get("elements", [])

                for u in elements:
                    uid = u.get("id")
                    email = u.get("email")
                    login = u.get("login")

                    if uid:
                        # Fallback to login if email hidden
                        final_email = email if email else f"{login}@placeholder"
                        user_map[uid] = final_email

            cache.set(cache_key, user_map, timeout=3600)
        except RequestException as e:
            logger.warning("OpenProject User Map failed: %s", e)
        return user_map

    def get_tickets(self, *, force_refresh=False):
        cache_key = f"openproject_{self.config.id}_active_packages_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.api_key:
            return []

        user_map = self._get_user_map()
        normalized_tickets = []

        try:
            self._fetch_work_packages(normalized_tickets, user_map)
            cache.set(cache_key, normalized_tickets, timeout=300)

        except RequestException:
            logger.exception("Error fetching OpenProject packages")
            return []
        else:
            return normalized_tickets

    def _fetch_work_packages(self, normalized_tickets, user_map):
        url = f"{self.base_url}/api/v3/work_packages"
        offset = 1
        page_size = 100
        max_pages = 100
        total_fetched = 0

        while offset <= max_pages:
            params = {
                "offset": offset,
                "pageSize": page_size,
                "sortBy": '[["updatedAt","desc"]]',
            }

            response = requests.get(
                url,
                auth=self.auth,
                params=params,
                headers=self._get_headers(),
                timeout=10,
            )
            response.raise_for_status()

            data = response.json()
            elements = data.get("_embedded", {}).get("elements", [])

            if not elements:
                break

            for item in elements:
                self._process_work_package(item, normalized_tickets, user_map)

            total_fetched += len(elements)

            if len(elements) < page_size:
                break

            offset += 1

        if offset > max_pages:
            logger.warning(
                "OpenProject fetch limit reached (%d items). "
                "Some older items may not be visible.",
                total_fetched,
            )

    def _process_work_package(self, item, normalized_tickets, user_map):
        links = item.get("_links", {})

        # Extract Email Logic
        assignee_link = links.get("assignee", {})
        assignee_href = assignee_link.get("href", "")
        assignee_name = assignee_link.get("title", "-")
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

        normalized_tickets.append(
            {
                "id": f"OP-{item.get('id')}",
                "title": item.get("subject"),
                "status": mapped_status,
                "priority": self._map_priority(
                    links.get("priority", {}).get("title", "Medium"),
                ),
                "origin": self.config.name,
                "customer": links.get("project", {}).get("title", "Project"),
                "group": "Project",
                "owner": assignee_name,
                "owner_email": assignee_email,
                "created_at": item.get("createdAt"),
                "updated_at": item.get("updatedAt"),
                "due_date": item.get("dueDate"),
                "url": f"{self.base_url}/work_packages/{item.get('id')}",
            },
        )

    def _map_status(self, status_text):
        s = str(status_text).lower()
        open_keywords = ["new", "open", "to do", "progress", "schedule"]
        if any(x in s for x in open_keywords):
            return "open"
        if any(x in s for x in ["closed", "done", "resolved", "reject"]):
            return "closed"
        return "pending"

    def _map_priority(self, priority_text):
        p = str(priority_text).lower()
        if any(x in p for x in ["immediate", "critical"]):
            return "Critical"
        if any(x in p for x in ["high", "urgent"]):
            return "High"
        if any(x in p for x in ["low"]):
            return "Low"
        return "Medium"
