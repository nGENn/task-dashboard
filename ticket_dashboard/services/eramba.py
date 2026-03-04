import concurrent.futures
import logging
from datetime import UTC
from datetime import datetime
from http import HTTPStatus

import requests
from django.core.cache import cache
from django.utils import timezone as django_timezone
from requests import RequestException
from requests.auth import HTTPBasicAuth

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


class ErambaService:
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

    def __init__(self, config):
        self.config = config
        self.base_url = config.api_url.rstrip("/")
        self.username = config.api_username
        self.password = config.api_password
        self.auth = (
            HTTPBasicAuth(self.username, self.password)
            if self.username and self.password
            else None
        )
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def check_health(self):
        start = django_timezone.now()
        if not self.auth:
            return {
                "name": self.config.name,
                "status": "auth_missing",
                "latency": 0,
                "error": "Missing Username or Password",
            }

        try:
            # Use the lightest endpoint for health check
            response = requests.get(
                f"{self.base_url}/api/security-incidents/index",
                headers=self.headers,
                auth=self.auth,
                params={"limit": 1},
                timeout=10,
            )
            response.raise_for_status()
        except RequestException as e:
            logger.warning(
                "Eramba health check failed for '%s': %s", self.config.name, e
            )
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

    def get_tickets(self, *, force_refresh=False):
        cache_key = f"eramba_{self.config.id}_active_items_cache"
        if not force_refresh:
            cached_data = cache.get(cache_key)
            if cached_data:
                return cached_data

        if not self.auth:
            return []

        modules_to_fetch = [
            {
                "module_api_path": "api/security-incidents",
                "model_class": "SecurityIncidents",
                "group_label": "Incident",
                "web_path": "security-incidents",
            },
            {
                "module_api_path": "api/projects",
                "model_class": "Projects",
                "group_label": "Project",
                "web_path": "projects",
            },
            {
                "module_api_path": "api/project-achievements",
                "model_class": "ProjectAchievements",
                "group_label": "Achievement",
                "web_path": "project-achievements",
            },
        ]

        all_normalized_tickets = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_module = {
                executor.submit(self._fetch_module, **module): module
                for module in modules_to_fetch
            }
            for future in concurrent.futures.as_completed(future_to_module):
                try:
                    module_tickets = future.result()
                    all_normalized_tickets.extend(module_tickets)
                except Exception:
                    module_name = future_to_module[future]["group_label"]
                    logger.exception("Failed to fetch Eramba module: %s", module_name)

        cache.set(cache_key, all_normalized_tickets, timeout=300)
        return all_normalized_tickets

    def _fetch_module(self, module_api_path, model_class, group_label, web_path):
        normalized_list = []
        try:
            page = 1
            limit = 100
            max_pages = 20

            while page <= max_pages:
                url = f"{self.base_url}/{module_api_path}/index"
                logger.debug("Fetching Eramba module: %s (Page %d)", url, page)
                response = requests.get(
                    url,
                    headers=self.headers,
                    auth=self.auth,
                    params={"page": page, "limit": limit},
                    timeout=30,
                )

                if response.status_code != HTTPStatus.OK:
                    logger.warning(
                        "Eramba module %s returned %s. URL: %s",
                        module_api_path,
                        response.status_code,
                        url,
                    )
                    break

                try:
                    data = response.json()
                except ValueError:
                    logger.exception(
                        "Eramba module %s returned invalid JSON. URL: %s",
                        module_api_path,
                        url,
                    )
                    break

                items = self._extract_items(data)
                if not items:
                    break

                for entry in items:
                    parsed = self._parse_item(entry, model_class, group_label, web_path)
                    if parsed:
                        normalized_list.append(parsed)

                if len(items) < limit:
                    break
                page += 1
        except RequestException as e:
            logger.warning(
                "Network error fetching Eramba module '%s': %s", module_api_path, e
            )

        return normalized_list

    def _extract_items(self, data):
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                return data["data"]
            if "items" in data and isinstance(data["items"], list):
                return data["items"]
        return []

    def _parse_item(self, entry, model_class, group_label, web_path):
        if not isinstance(entry, dict):
            return None

        # Unwrap common Eramba response patterns
        item = entry
        keys = list(entry.keys())
        if (
            len(keys) == 1
            and (keys[0] == model_class or keys[0] in self.POSSIBLE_WRAPPERS)
            and isinstance(entry[keys[0]], dict)
        ):
            item = entry[keys[0]]

        if "id" not in item:
            item = entry if "id" in entry else None
            if not item:
                return None

        # Resolve title with module-specific fallbacks
        title = None
        if "AssetReview" in model_class:
            fk = item.get("foreign_key")
            if isinstance(fk, int):
                title = ASSET_TYPE_MAP.get(fk)

        if not title:
            title = (
                item.get("title")
                or item.get("name")
                or f"{group_label} #{item.get('id')}"
            )

        view_url = f"{self.base_url}/{web_path}/view/{model_class}/{item.get('id')}"

        return {
            "id": f"ERA-{group_label[:3].upper()}-{item.get('id')}",
            "title": str(title)[:250],  # Ensure within DB limits
            "status": self._determine_status(item),
            "priority": self._determine_priority(item),
            "origin": self.config.name,
            "customer": "Internal",
            "group": group_label,
            "owner": self._parse_owners(item.get("owners", []))[:250],
            "created_at": self._format_date(
                item.get("created") or item.get("open_date") or item.get("start")
            ),
            "updated_at": self._format_date(item.get("modified")),
            "due_date": self._format_date(
                # Prioritize planned_date for reviews
                item.get("planned_date")
                or item.get("deadline")
                or item.get("end")
                or item.get("planned_end")
            ),
            "url": view_url[:500],
        }

    def _determine_status(self, item):
        status_raw = str(item.get("status", "")).lower()
        pid = item.get("project_status_id")

        # Project status: 1=Planned, 2=Ongoing, 3=Done
        status_done = 3
        status_planned = 1

        if (
            item.get("closure_date")
            or item.get("actual_date")
            or any(x in status_raw for x in ["close", "completed"])
            or pid == status_done
        ):
            return "closed"

        if pid == status_planned or any(x in status_raw for x in ["pending", "plan"]):
            return "pending"

        return "open"

    def _determine_priority(self, item):
        # Eramba uses custom fields for priority often
        custom_prio = item.get("custom_field_9")
        if isinstance(custom_prio, dict) and custom_prio.get("value"):
            return str(custom_prio["value"]).capitalize()
        return "Medium"

    def _parse_owners(self, owners_field):
        if not isinstance(owners_field, list) or not owners_field:
            return "GRC Team"

        names = []
        for o in owners_field:
            if isinstance(o, dict) and o.get("user"):
                u = o["user"]
                if isinstance(u, dict):
                    email = u.get("email")
                    full_name = f"{u.get('name', '')} {u.get('surname', '')}".strip()
                    names.append(email or full_name)
                else:
                    names.append(str(u))
            else:
                names.append(str(o))

        return ", ".join(filter(None, names)) or "GRC Team"

    def _format_date(self, date_str):
        if not date_str:
            return ""

        if isinstance(date_str, str) and "T" in date_str:
            try:
                return datetime.fromisoformat(date_str).isoformat()
            except ValueError:
                pass

        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(str(date_str), fmt).replace(tzinfo=UTC)
                return dt.isoformat()
            except (ValueError, TypeError):
                continue

        return str(date_str)
