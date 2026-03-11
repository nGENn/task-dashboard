import asyncio
import base64
import logging
from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import httpx
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)

ASSET_TYPE_MAP = {
    2: "Backup System",
    3: "Mail System",
    4: "ERP-Software",
    5: "CRM",
    6: "Network Devices",
    7: "Firewalls",
    8: "Server",
    9: "Office",
    10: "Apple IOS Devices",
    11: "PC Windows",
    12: "PC MacOS",
    13: "Server Room",
    14: "IoT Devices",
    15: "Logs",
    16: "Metrics",
    17: "Analytics data website",
    18: "Web Services",
    19: "Asset data",
    20: "CRM Data",
    21: "Knowledge base data",
    22: "Customer oriented content",
    23: "Commercial data",
    24: "Marketing data",
    25: "Password/User-identity data",
    26: "Calendar data",
    27: "Git Server",
    28: "Container registry",
    29: "CI/CD Server",
    30: "Source Code",
    31: "Configuration data",
    32: "GRC data",
    33: "Project management data",
    34: "Reverse Proxy",
    35: "Certificates",
    36: "Sichkon",
    37: "Ticket data",
    38: "Employee data",
    39: "Security Keys",
    40: "Storage Media",
}

# Eramba Magic Numbers for Project Status
STATUS_PLANNED = 1
STATUS_DONE = 3

# How many days into the future to consider "Open" tasks relevant.
# Can be overridden in Django settings.
OPEN_TASK_FUTURE_WINDOW_DAYS = getattr(
    settings, "ERAMBA_OPEN_TASK_FUTURE_WINDOW_DAYS", 30
)


class ErambaService:
    """
    Integration service for Eramba GRC.
    Handles parallel fetching of incidents, projects, and various reviews.
    """

    # Eramba often wraps API items in a dictionary named after the model class or 'Item'
    POSSIBLE_WRAPPERS = {
        "Item",
        "SecurityIncident",
        "SecurityIncidents",
        "Project",
        "Projects",
        "SecurityServiceAudit",
        "SecurityServiceAudits",
        "SecurityPolicyReview",
        "SecurityPolicyReviews",
        "AssetReview",
        "AssetReviews",
        "RiskReview",
        "RiskReviews",
        "ThirdPartyRiskReview",
        "ThirdPartyRiskReviews",
        "BusinessContinuityReview",
        "BusinessContinuityReviews",
        "ProjectAchievement",
        "ProjectAchievements",
    }

    # Configuration for modules to synchronize
    FETCH_CONFIG = [
        {
            "path": "api/security-incidents",
            "model": "SecurityIncidents",
            "label": "Incident",
            "web": "security-incidents",
        },
        {
            "path": "api/projects",
            "model": "Projects",
            "label": "Project",
            "web": "projects",
        },
        {
            "path": "api/project-achievements",
            "model": "ProjectAchievements",
            "label": "Achievement",
            "web": "project-achievements",
        },
        {
            "path": "api/asset-reviews",
            "model": "AssetReviews",
            "label": "Asset Review",
            "web": "asset-reviews",
        },
        {
            "path": "api/risk-reviews",
            "model": "RiskReviews",
            "label": "Risk Review",
            "web": "risk-reviews",
        },
        {
            "path": "api/third-party-risk-reviews",
            "model": "ThirdPartyRiskReviews",
            "label": "Third Party Risk Review",
            "web": "third-party-risk-reviews",
        },
        {
            "path": "api/business-continuity-reviews",
            "model": "BusinessContinuityReviews",
            "label": "Business Continuity Review",
            "web": "business-continuity-reviews",
        },
        {
            "path": "api/security-policy-reviews",
            "model": "SecurityPolicyReviews",
            "label": "Policy Review",
            "web": "security-policy-reviews",
        },
        {
            "path": "api/security-service-audits",
            "model": "SecurityServiceAudits",
            "label": "Service Audit",
            "web": "security-service-audits",
        },
    ]

    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url.rstrip("/")
        self.username = config.api_username
        self.password = config.api_password

        # Mandatory headers for Eramba API stability
        self.headers = {
            "Accept": "application/json",
            "Cookie": "translation=1",
        }

        # Manual Basic Auth encoding is required because Eramba redirects 302 to /login
        # instead of sending a 401 challenge, which prevents standard httpx auth
        # negotiation.
        if self.username and self.password:
            auth_bytes = f"{self.username}:{self.password}".encode("ascii")
            encoded_auth = base64.b64encode(auth_bytes).decode("ascii")
            self.headers["Authorization"] = f"Basic {encoded_auth}"

    def get_tasks(self, *, force_refresh=False):
        """Synchronous wrapper for get_tasks_async."""
        return asyncio.run(self.get_tasks_async(force_refresh=force_refresh))

    async def get_tasks_async(self, *, force_refresh=False):
        """Fetches and normalizes tasks from all configured Eramba modules."""
        cache_key = f"eramba_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if "Authorization" not in self.headers:
            return []

        # Calculate future limit once for this sync run
        future_limit = django_timezone.now() + django_timezone.timedelta(
            days=OPEN_TASK_FUTURE_WINDOW_DAYS
        )

        # follow_redirects=False ensures we fail fast if authentication is rejected
        async with httpx.AsyncClient(follow_redirects=False) as client:
            await self._fetch_groups(client)
            tasks = [
                self._fetch_module(client, module, future_limit)
                for module in self.FETCH_CONFIG
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            all_tasks = []
            for res in results:
                if isinstance(res, list):
                    all_tasks.extend(res)
                else:
                    logger.error(
                        "Eramba sync error for service %s: %s", self.config.name, res
                    )

            cache.set(cache_key, all_tasks, timeout=300)
            return all_tasks

    async def _fetch_groups(self, client):
        """Fetches Eramba groups and maps their members (excluding API users)."""
        if hasattr(self, "group_members_map"):
            return

        self.group_members_map = {}
        page = 1
        url = f"{self.base_url}/api/groups/index"

        while True:
            try:
                resp = await client.get(
                    url,
                    headers=self.headers,
                    params={"page": page},
                    timeout=10.0,
                )
                if resp.status_code != HTTPStatus.OK:
                    break

                data = resp.json()
                groups_list, pagination = self._extract_groups_and_pagination(data)

                for group in groups_list:
                    if isinstance(group, dict):
                        group_id = group.get("id")
                        self.group_members_map[group_id] = self._process_group_users(
                            group.get("users", [])
                        )

                if not pagination.get("has_next_page"):
                    break
                page += 1
            except (httpx.HTTPError, ValueError) as e:
                logger.warning(
                    "Failed to fetch Eramba groups for %s: %s", self.config.name, e
                )
                break

    def _extract_groups_and_pagination(self, data):
        """Helper to extract groups list and pagination from response data."""
        if isinstance(data, dict):
            return data.get("data") or [], data.get("pagination", {})
        if isinstance(data, list):
            return data, {}
        return [], {}

    def _process_group_users(self, users):
        """Filters and extracts member identifiers from a list of group users."""
        members = []
        for user in users:
            if not isinstance(user, dict):
                continue

            # Exclude API and system/task users based on naming conventions
            u_info = [
                str(user.get(k, "")).lower()
                for k in ["name", "surname", "login", "email"]
            ]
            if any("api" in x or "task" in x for x in u_info):
                continue

            if user.get("email"):
                members.append(user["email"])
            else:
                full_name = f"{user.get('name', '')} {user.get('surname', '')}".strip()
                if full_name:
                    members.append(full_name)
        return members

    async def _fetch_module(self, client, module_config, future_limit):
        """Fetches a specific module with full pagination support."""
        api_path = module_config["path"]
        model_class = module_config["model"]
        label = module_config["label"]
        web_path = module_config["web"]

        normalized_list = []
        url = f"{self.base_url}/{api_path}/index"
        limit = 100
        page = 1

        try:
            while True:
                resp = await client.get(
                    url,
                    headers=self.headers,
                    params={"page": page, "limit": limit},
                    timeout=30.0,
                )

                if resp.status_code != HTTPStatus.OK:
                    break

                # Fail if server returned HTML (likely a login or error page)
                content_type = resp.headers.get("Content-Type", "").lower()
                if "html" in content_type:
                    logger.warning(
                        "Eramba %s module request for %s returned HTML (Auth rejected)",
                        api_path,
                        self.config.name,
                    )
                    break

                try:
                    data = resp.json()
                except (ValueError, TypeError):
                    break

                items = self._extract_items(data)
                if not items:
                    break

                for entry in items:
                    parsed = self._parse_item(
                        entry, model_class, label, web_path, future_limit
                    )
                    if parsed:
                        normalized_list.append(parsed)

                if len(items) < limit:
                    break
                page += 1

        except (httpx.HTTPError, ValueError) as e:
            logger.warning(
                "Error fetching Eramba module '%s' for %s: %s",
                api_path,
                self.config.name,
                e,
            )

        return normalized_list

    def _extract_items(self, data):
        """Normalizes various Eramba JSON response formats into a flat item list."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data") or data.get("items") or []
        return []

    def _parse_item(self, entry, model_class, group_label, web_path, future_limit):
        """Parses a single Eramba item into the dashboard task format."""
        if not isinstance(entry, dict):
            return None

        # Handle Eramba's nested item wrapping (e.g. {"Projects": {...}})
        item = self._unwrap_entry(entry)
        item_id = item.get("id")
        if item_id is None:
            return None

        # Determine status first to allow filtering
        status = self._determine_status(item)

        # Filtering logic: Only keep open tasks that are due within the window
        if status == "open":
            due_dt = self._parse_date_to_dt(self._get_due_date_raw(item))
            if due_dt and due_dt > future_limit:
                return None

        title = self._get_item_title(item, model_class, group_label, item_id)
        view_url = f"{self.base_url}/{web_path}/view/{model_class}/{item_id}"

        return {
            "id": f"ERA-{group_label[:3].upper()}-{item_id}",
            "title": str(title)[:250],
            "status": status,
            "priority": self._determine_priority(item),
            "origin": self.config.name,
            "customer": "Internal",
            "group": group_label,
            "owner": self._parse_owners(self._get_owners_raw(item))[:250],
            "created_at": self._format_date(
                item.get("created") or item.get("open_date") or item.get("start")
            ),
            "updated_at": self._format_date(item.get("modified")),
            "due_date": self._format_date(self._get_due_date_raw(item)),
            "url": view_url[:500],
            "extra_info": {"module": model_class},
        }

    def _unwrap_entry(self, entry):
        """Unwraps a nested Eramba entry if it's wrapped by model class name."""
        first_key = next(iter(entry.keys())) if entry else None
        if (
            len(entry) == 1
            and first_key in self.POSSIBLE_WRAPPERS
            and isinstance(entry[first_key], dict)
        ):
            return entry[first_key]
        return entry

    def _get_due_date_raw(self, item):
        """Extracts the first available due date field from an Eramba item."""
        return (
            item.get("planned_date")
            or item.get("deadline")
            or item.get("end")
            or item.get("planned_end")
        )

    def _get_owners_raw(self, item):
        """Collects all potential owner/reviewer data from an Eramba item."""
        owners_raw = []
        possible_fields = [
            "owners",
            "reviewers",
            "task_owners",
            "audit_reviewers",
            "service_auditors",
            "audit_evidence_owners",
            "audit_owners",
        ]
        for f in possible_fields:
            val = item.get(f)
            if isinstance(val, list):
                owners_raw.extend(val)
            elif val and isinstance(val, (dict, str)):
                owners_raw.append(val)
        return owners_raw

    def _get_item_title(self, item, model_class, group_label, item_id):
        """Determines the display title for an Eramba item."""
        title = None
        if "AssetReview" in model_class:
            fk = item.get("foreign_key")
            if isinstance(fk, int):
                title = ASSET_TYPE_MAP.get(fk)

        return (
            title
            or item.get("title")
            or item.get("name")
            or f"{group_label} #{item_id}"
        )

    def _determine_status(self, item):
        """Maps Eramba status fields and magic numbers to dashboard statuses."""
        status_raw = str(item.get("status", "")).lower()
        pid = item.get("project_status_id")

        if (
            item.get("closure_date")
            or item.get("actual_date")
            or any(x in status_raw for x in ["close", "completed"])
            or pid == STATUS_DONE
        ):
            return "closed"

        if pid == STATUS_PLANNED or any(x in status_raw for x in ["pending", "plan"]):
            return "pending"

        return "open"

    def _determine_priority(self, item):
        """Extracts priority from Eramba custom field 9 (default priority field)."""
        custom_prio = item.get("custom_field_9")
        if isinstance(custom_prio, dict) and custom_prio.get("value"):
            return str(custom_prio["value"]).capitalize()
        return "Medium"

    def _parse_owners(self, owners_field):
        """Extracts names, emails, or group names from various owner object formats."""
        if not isinstance(owners_field, list) or not owners_field:
            return "-"

        names = []
        for o in owners_field:
            if not isinstance(o, dict):
                names.append(str(o))
                continue

            # 1. Try to find a nested 'user' object
            user = o.get("user")
            if isinstance(user, dict):
                email = user.get("email")
                full_name = f"{user.get('name', '')} {user.get('surname', '')}".strip()
                names.append(email or full_name)
                continue

            # 2. Try to find a nested 'group' object or 'Group' object_model
            group = o.get("group")
            obj_model = str(o.get("object_model", "")).lower()
            obj_id = o.get("object_id")

            if isinstance(group, dict) or obj_model == "group":
                g_id = group.get("id") if isinstance(group, dict) else obj_id

                # Expand to group members if available
                if (
                    hasattr(self, "group_members_map")
                    and g_id in self.group_members_map
                ):
                    members = self.group_members_map[g_id]
                    if members:
                        names.extend(members)
                        continue

                # Fallback to group name if expansion failed
                g_name = (
                    group.get("name") if isinstance(group, dict) else None
                ) or f"Group #{g_id}"
                names.append(g_name)
                continue

            # 3. Fallback: check for 'name' or 'email' at the top level
            display_name = o.get("name") or o.get("email") or o.get("display_name")
            if display_name:
                names.append(str(display_name))
                continue

            # 4. Final fallback: use IDs if we can't find anything else
            if o.get("id") and o.get("model"):
                names.append(f"{o.get('model')} #{o.get('id')}")

        return ", ".join(filter(None, names)) or "-"

    def _format_date(self, date_str):
        """Standardizes Eramba dates to ISO format."""
        dt = self._parse_date_to_dt(date_str)
        return dt.isoformat() if dt else str(date_str or "")

    def _parse_date_to_dt(self, date_str):
        """Parses various Eramba date formats into a timezone-aware datetime object."""
        if not date_str:
            return None

        if isinstance(date_str, str) and "T" in date_str:
            try:
                dt = datetime.fromisoformat(date_str)
                return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
            except ValueError:
                pass

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(date_str), fmt).replace(tzinfo=UTC)
            except (ValueError, TypeError):
                continue

        return None

    def check_health(self):
        """Verifies API connectivity and authentication status."""
        start = django_timezone.now()
        if "Authorization" not in self.headers:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing Credentials",
            }

        try:
            response = httpx.get(
                f"{self.base_url}/api/security-incidents/index",
                headers=self.headers,
                params={"limit": 1},
                timeout=10.0,
                follow_redirects=False,
            )

            if response.status_code == HTTPStatus.FOUND:
                loc = response.headers.get("Location")
                return {
                    "name": self.config.name,
                    "status": "offline",
                    "latency": 0,
                    "error": f"Auth Failure (Redirected to {loc})",
                }

            content_type = response.headers.get("Content-Type", "").lower()
            if "html" in content_type:
                return {
                    "name": self.config.name,
                    "status": "offline",
                    "latency": 0,
                    "error": "Auth Failure (HTML returned)",
                }

            response.raise_for_status()
            response.json()
        except (httpx.HTTPError, ValueError) as e:
            return {
                "name": self.config.name,
                "status": "offline",
                "latency": 0,
                "error": str(e),
            }
        else:
            latency = int((django_timezone.now() - start).total_seconds() * 1000)
            return {
                "name": self.config.name,
                "status": "online",
                "latency": latency,
                "error": None,
            }
