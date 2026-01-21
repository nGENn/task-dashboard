import logging
from datetime import datetime
from http import HTTPStatus

import requests
from django.core.cache import cache
from django.utils import timezone as django_timezone
from requests import RequestException

logger = logging.getLogger(__name__)


class GitLabService:
    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url or "https://gitlab.com"
        self.token = config.api_token
        self.headers = {"Private-Token": self.token}

    def get_tickets(self, *, force_refresh=False):
        """
        Fetches ALL open Issues and Merge Requests.
        Enriches them with real Emails for security filtering.
        """
        cache_key = f"gitlab_{self.config.id}_active_items_cache"

        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.token:
            logger.warning("GitLab credentials missing.")
            return []

        # 1. FETCH USER MAP (ID -> Email)
        # We need this because MR lists don't include emails,
        # but we need email for filtering.
        user_map = self._get_user_map()

        normalized_items = []

        try:
            # 2. FETCH ISSUES
            issues_url = f"{self.base_url}/api/v4/issues"
            issues_params = {
                "scope": "all",
                "state": "opened",
                "order_by": "updated_at",
            }
            self._fetch_and_normalize(
                issues_url,
                "Issue",
                normalized_items,
                user_map,
                params=issues_params,
            )

            # 3. FETCH MERGE REQUESTS
            mrs_url = f"{self.base_url}/api/v4/merge_requests"
            mrs_params = {
                "scope": "all",
                "state": "opened",
                "order_by": "updated_at",
            }
            self._fetch_and_normalize(
                mrs_url,
                "Merge Request",
                normalized_items,
                user_map,
                params=mrs_params,
            )

            # Save combined list to cache (5 mins)
            cache.set(cache_key, normalized_items, timeout=300)
            return normalized_items  # noqa: TRY300

        except RequestException:
            logger.exception("Error fetching GitLab data")
            return []

    def _get_user_map(self):
        """
        Fetches all users to create a {gitlab_id: 'email@company.com'}
        lookup dict. Cached for longer (1 hour) because user emails
        rarely change.
        """
        map_cache_key = f"gitlab_{self.config.id}_user_email_map"
        cached_map = cache.get(map_cache_key)
        if cached_map:
            return cached_map

        user_map = {}
        try:
            # Fetch users (Admin token required to see emails)
            url = f"{self.base_url}/api/v4/users?per_page=100&active=true"
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == HTTPStatus.OK:
                for u in response.json():
                    # Map ID to Public Email (or primary email if admin)
                    email = u.get("public_email") or u.get("email")
                    if email:
                        user_map[u["id"]] = email

            cache.set(map_cache_key, user_map, timeout=3600)
        except RequestException as e:
            logger.warning("Failed to build GitLab user map: %s", e)

        return user_map

    def _fetch_and_normalize(
        self, url, item_type, target_list, user_map, params=None
    ):
        try:
            page = 1
            per_page = 100
            max_pages = 100
            total_fetched = 0

            while page <= max_pages:
                request_params = (params or {}).copy()
                request_params.update({
                    "page": page,
                    "per_page": per_page,
                })

                response = requests.get(
                    url,
                    headers=self.headers,
                    params=request_params,
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()

                if not data:
                    break

                for item in data:
                    # Distinguish IDs: GL-I-123 (Issue) vs
                    # GL-MR-123 (Merge Request)
                    prefix = (
                        "GL-MR" if item_type == "Merge Request" else "GL-I"
                    )
                    title_prefix = (
                        "[MR] " if item_type == "Merge Request" else ""
                    )

                    # Determine Owner (Assignee)
                    assignee_data = item.get("assignee")
                    owner_name = "-"
                    owner_email = None

                    if assignee_data:
                        owner_name = assignee_data.get("name")
                        # LOOKUP EMAIL FROM OUR MAP
                        user_id = assignee_data.get("id")
                        owner_email = user_map.get(user_id)

                    # Determine Group (Project Namespace)
                    full_ref = item.get("references", {}).get("full", "")
                    group_name = (
                        full_ref.split("#")[0]
                        if "#" in full_ref
                        else "GitLab"
                    )

                    target_list.append(
                        {
                            "id": f"{prefix}-{item.get('iid')}",
                            "title": f"{title_prefix}{item.get('title')}",
                            "status": "open",
                            "priority": self._extract_priority(
                                item.get("labels", [])
                            ),
                            "origin": self.config.name,
                            "customer": group_name.split("/")[0]
                            if "/" in group_name
                            else group_name,
                            "group": group_name,
                            "owner": owner_name,
                            "owner_email": owner_email,
                            "created_at": self._format_date(
                                item.get("created_at"),
                            ),
                            "updated_at": self._format_date(
                                item.get("updated_at"),
                            ),
                            "due_date": self._format_date(
                                item.get("due_date"),
                            ),
                            "url": item.get("web_url"),
                        },
                    )
                total_fetched += len(data)

                if len(data) < per_page:
                    break

                page += 1

            if page > max_pages:
                logger.warning(
                    "GitLab %s fetch limit reached (%d items). "
                    "Some older items may not be visible.",
                    item_type,
                    total_fetched,
                )

        except RequestException as e:
            logger.warning("Failed to fetch GitLab %s: %s", item_type, e)

    def _extract_priority(self, labels):
        labels = [lab.lower() for lab in labels]
        if any("critical" in lab or "urgent" in lab for lab in labels):
            return "Critical"
        if any("high" in lab for lab in labels):
            return "High"
        if any("low" in lab for lab in labels):
            return "Low"
        return "Medium"

    def _format_date(self, date_str):
        if not date_str:
            return ""
        try:
            # Ensure it has offset for fromisoformat
            dt_str = date_str.replace("Z", "+00:00")
            datetime.fromisoformat(dt_str)
            return dt_str
        except (ValueError, TypeError):
            return date_str

    def check_health(self):
        start = django_timezone.now()

        if not self.token:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing Token in configuration",
            }

        try:
            response = requests.get(
                f"{self.base_url}/api/v4/user",
                headers=self.headers,
                timeout=3,
            )
            response.raise_for_status()

            latency = int(
                (django_timezone.now() - start).total_seconds() * 1000,
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
