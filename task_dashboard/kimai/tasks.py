"""
Django-Q2 background tasks for Kimai integration.

T06: refresh_kimai_user_cache
T08: sync_kimai_teams
T10: sync_kimai_activities_for_service
T11: sync_kimai_activities (fan-out)
T12: run_reminder_evaluation
"""

import asyncio
import logging
from datetime import UTC
from datetime import datetime

from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from task_dashboard.users.models import ExternalGroup
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.models import User

from .client import KimaiClient
from .holidays import get_public_holidays
from .models import KimaiSettings
from .reminder import calc_days_behind
from .reminder import parse_working_days

logger = logging.getLogger(__name__)

CACHE_TTL_EMAIL_MAP = 86400  # 24h
CACHE_TTL_REMINDER = 3600  # 1h
CACHE_TTL_TEAM_MAP = 3600  # 1h

KIMAI_ACTIVITY_COMMENT_SEP = ":"


def _get_kimai_client() -> KimaiClient | None:
    """Return KimaiClient from active Kimai ServiceConfiguration, or None."""
    config = ServiceConfiguration.objects.filter(
        service_type="kimai", is_active=True
    ).first()
    if not config:
        logger.warning("No active Kimai ServiceConfiguration found.")
        return None
    return KimaiClient(base_url=config.api_url, api_token=config.api_token or "")


# ---------------------------------------------------------------------------
# T06 — refresh_kimai_user_cache
# ---------------------------------------------------------------------------


def refresh_kimai_user_cache() -> int:
    """
    Fetch /api/users from Kimai and populate kimai_email_map in Valkey (V16).
    Maps email (from user.title) → kimai_user_id.
    Returns count of users cached.
    """
    client = _get_kimai_client()
    if not client:
        return 0

    try:
        users = async_to_sync(client.get_users)()
    except Exception:
        logger.exception("Failed to fetch Kimai users")
        return 0

    email_map = {}
    for u in users:
        email = (u.get("title") or "").strip().lower()
        uid = u.get("id")
        if email and uid:
            email_map[email] = uid

    cache.set("kimai_email_map", email_map, timeout=CACHE_TTL_EMAIL_MAP)
    logger.info("Cached %d Kimai user email mappings.", len(email_map))
    return len(email_map)


def get_kimai_email_map() -> dict[str, int]:
    cached = cache.get("kimai_email_map")
    if cached is not None:
        return cached
    # Refresh on-demand if cache empty
    refresh_kimai_user_cache()
    return cache.get("kimai_email_map") or {}


# ---------------------------------------------------------------------------
# T08 — sync_kimai_teams
# ---------------------------------------------------------------------------


def sync_kimai_teams() -> int:
    """
    Sync ExternalGroups to Kimai teams for group-scoped activity visibility (V10-V13).

    Team name format: "{ExternalGroup.origin}::{ExternalGroup.name}" (V11).
    Only Django groups with sso-* prefix are mapped to Kimai team users (V10).
    Idempotent: creates team if missing, patches membership if changed (V13).
    Returns count of teams processed.
    """
    settings = KimaiSettings.load()
    if not settings.team_sync_enabled:
        return 0

    client = _get_kimai_client()
    if not client:
        return 0

    email_map = get_kimai_email_map()
    if not email_map:
        logger.warning("kimai_email_map empty — skipping team sync")
        return 0

    return async_to_sync(_sync_kimai_teams_async)(client, email_map)


async def _sync_kimai_teams_async(
    client: KimaiClient, email_map: dict[str, int]
) -> int:
    existing_teams = await client.get_teams()
    # Build map: team_name → team dict
    team_by_name: dict[str, dict] = {t["name"]: t for t in existing_teams}

    external_groups = await asyncio.to_thread(
        lambda: list(ExternalGroup.objects.all().select_related())
    )

    processed = 0
    team_map: dict[str, int] = {}

    for eg in external_groups:
        team_name = f"{eg.origin}::{eg.name}"

        # Collect Kimai user IDs from Django groups mapped to this ExternalGroup
        # Only sso-* prefixed Django groups are allowed (V10)
        django_groups = await asyncio.to_thread(
            lambda eg=eg: list(
                Group.objects.filter(
                    name__startswith="sso-",
                    taskpermission__allowed_external_group=eg,
                ).distinct()
            )
        )

        kimai_user_ids = set()
        for dg in django_groups:
            members = await asyncio.to_thread(
                lambda dg=dg: list(
                    User.objects.filter(groups=dg).values_list("email", flat=True)
                )
            )
            for email in members:
                uid = email_map.get(email.lower())
                if uid:
                    kimai_user_ids.add(uid)

        if team_name in team_by_name:
            existing_team = team_by_name[team_name]
            existing_ids = {u["id"] for u in (existing_team.get("users") or [])}
            team_id = existing_team["id"]

            if existing_ids != kimai_user_ids:
                try:
                    await client.patch_team(
                        team_id,
                        {"users": [{"id": uid} for uid in kimai_user_ids]},
                    )
                    logger.info(
                        "Updated Kimai team %r: %d members",
                        team_name,
                        len(kimai_user_ids),
                    )
                except Exception:
                    logger.exception("Failed to patch Kimai team %r", team_name)
        else:
            try:
                new_team = await client.create_team(
                    {
                        "name": team_name,
                        "users": [{"id": uid} for uid in kimai_user_ids],
                    }
                )
                team_id = new_team["id"]
                logger.info(
                    "Created Kimai team %r with %d members",
                    team_name,
                    len(kimai_user_ids),
                )
            except Exception:
                logger.exception("Failed to create Kimai team %r", team_name)
                continue

        team_map[eg.name] = team_id
        processed += 1

    cache.set("kimai_team_map", team_map, timeout=CACHE_TTL_TEAM_MAP)
    return processed


# ---------------------------------------------------------------------------
# T10 — sync_kimai_activities_for_service
# ---------------------------------------------------------------------------


def _activity_comment(config_id: int, external_task_id: str) -> str:
    """V3: activity.comment = "{service_config_id}:{external_task_id}" """
    return f"{config_id}{KIMAI_ACTIVITY_COMMENT_SEP}{external_task_id}"


def _parse_activity_comment(comment: str | None) -> tuple[int, str] | None:
    """Parse activity comment → (config_id, external_task_id) or None."""
    if not comment:
        return None
    parts = comment.split(KIMAI_ACTIVITY_COMMENT_SEP, 1)
    if len(parts) != 2:  # noqa: PLR2004
        return None
    try:
        return int(parts[0]), parts[1]
    except ValueError:
        return None


def sync_kimai_activities_for_service(config_id: int) -> int:
    """
    Sync tasks from one ServiceConfiguration into Kimai activities (T10).
    Uses ExternalGroup.origin (= config.name) → Kimai project per group.
    Returns count of activities processed.
    """
    settings = KimaiSettings.load()
    if not settings.sync_enabled:
        return 0

    try:
        config = ServiceConfiguration.objects.get(pk=config_id, is_active=True)
    except ServiceConfiguration.DoesNotExist:
        logger.warning("ServiceConfiguration %d not found or inactive.", config_id)
        return 0

    if config.service_type == "kimai":
        return 0

    client = _get_kimai_client()
    if not client:
        return 0

    return async_to_sync(_sync_activities_async)(client, config, settings)


async def _sync_activities_async(  # noqa: C901, PLR0912, PLR0915
    client: KimaiClient, config: ServiceConfiguration, settings: KimaiSettings
) -> int:
    # Fetch all Kimai customers and projects once
    try:
        customers = await client.get_customers()
        all_projects = await client.get_projects()
    except Exception:
        logger.exception(
            "Failed to fetch Kimai customers/projects for config %s", config.name
        )
        return 0

    customer_by_name: dict[str, dict] = {c["name"]: c for c in customers}
    project_by_comment: dict[str, dict] = {
        p["comment"]: p for p in all_projects if p.get("comment")
    }

    # Group tasks by ExternalGroup name
    tasks_qs = await asyncio.to_thread(
        lambda: list(
            Task.objects.filter(service=config)
            .select_related("service_group")
            .values(
                "external_id",
                "title",
                "status",
                "service_group__name",
                "service_group__origin",
                "customer",
            )
        )
    )

    # Group by external group name
    by_group: dict[str, list[dict]] = {}
    ungrouped = []
    for t in tasks_qs:
        group_name = t.get("service_group__name") or ""
        if group_name:
            by_group.setdefault(group_name, []).append(t)
        else:
            ungrouped.append(t)

    # Treat ungrouped tasks under a synthetic group named after the config
    if ungrouped:
        by_group.setdefault(config.name, []).extend(ungrouped)

    processed = 0
    for group_name, group_tasks in by_group.items():
        # Project comment key = "{config.name}::{group_name}"
        project_comment_key = f"{config.name}::{group_name}"

        kimai_project = project_by_comment.get(project_comment_key)
        if not kimai_project:
            # Determine customer: use first task's customer field or config.name
            customer_name = group_tasks[0].get("customer") or config.name
            kimai_customer = customer_by_name.get(customer_name)
            if not kimai_customer:
                try:
                    global_settings = await GlobalSetting.objects.afirst()
                    kimai_customer = await client.create_customer(
                        {
                            "name": customer_name,
                            "visible": True,
                            "currency": "EUR",
                            "country": global_settings.kimai_customer_country if global_settings else "DE",
                            "timezone": global_settings.kimai_customer_timezone if global_settings else "Europe/Berlin",
                        }
                    )
                    customer_by_name[customer_name] = kimai_customer
                except Exception:
                    logger.exception(
                        "Failed to create Kimai customer %r", customer_name
                    )
                    continue

            try:
                kimai_project = await client.create_project(
                    {
                        "name": group_name,
                        "customer": kimai_customer["id"],
                        "comment": project_comment_key,
                        "visible": True,
                    }
                )
                project_by_comment[project_comment_key] = kimai_project
            except Exception:
                logger.exception("Failed to create Kimai project %r", group_name)
                continue

        project_id = kimai_project["id"]

        try:
            existing_activities = await client.get_activities(project_id)
        except Exception:
            logger.exception("Failed to fetch activities for project %d", project_id)
            continue

        # Build map: comment → activity dict (only our synced ones via V3)
        activity_by_comment: dict[str, dict] = {}
        for act in existing_activities:
            parsed = _parse_activity_comment(act.get("comment"))
            if parsed and parsed[0] == config.id:
                activity_by_comment[act["comment"]] = act

        # Current task IDs in this group
        current_comments = {
            _activity_comment(config.id, t["external_id"]): t for t in group_tasks
        }

        # Hide activities whose source task no longer exists / is closed (V12, C13)
        for comment, act in activity_by_comment.items():
            task_data = current_comments.get(comment)
            if task_data is None or task_data.get("status") == "closed":
                if act.get("visible") is not False:
                    try:
                        await client.patch_activity(act["id"], {"visible": False})
                    except Exception:
                        logger.exception("Failed to hide activity %d", act["id"])
            # Re-show if previously hidden (V12)
            elif act.get("visible") is False:
                try:
                    await client.patch_activity(act["id"], {"visible": True})
                except Exception:
                    logger.exception("Failed to unhide activity %d", act["id"])

        # Create/update open tasks (V12: only non-closed)
        for comment, task_data in current_comments.items():
            if task_data.get("status") == "closed":
                continue

            existing = activity_by_comment.get(comment)
            title = task_data.get("title") or task_data["external_id"]

            if existing:
                if existing.get("name") != title:
                    try:
                        await client.patch_activity(existing["id"], {"name": title})
                    except Exception:
                        logger.exception(
                            "Failed to update activity name for %s", comment
                        )
            else:
                try:
                    await client.create_activity(
                        {
                            "name": title,
                            "project": project_id,
                            "comment": comment,
                            "visible": True,
                        }
                    )
                    processed += 1
                except Exception:
                    logger.exception("Failed to create activity for task %s", comment)

    return processed


def sync_kimai_activities() -> int:
    """Fan-out: sync activities for every active non-kimai service."""
    from django_q.tasks import async_task  # noqa: PLC0415

    configs = ServiceConfiguration.objects.filter(is_active=True).exclude(
        service_type="kimai"
    )
    count = 0
    for config in configs:
        async_task(
            "task_dashboard.kimai.tasks.sync_kimai_activities_for_service", config.id
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# T12 — run_reminder_evaluation
# ---------------------------------------------------------------------------


def run_reminder_evaluation() -> int:  # noqa: C901, PLR0912
    """
    For every Django user with a matching Kimai account, compute days_behind
    and write to kimai_reminder:{user_pk} Valkey cache (V5, V6, V9).
    Returns count of users evaluated.
    """
    settings = KimaiSettings.load()
    if not settings.reminder_enabled:
        return 0

    client = _get_kimai_client()
    if not client:
        return 0

    exempt = settings.get_exempt_email_set()
    email_map = get_kimai_email_map()
    if not email_map:
        logger.warning("kimai_email_map empty — skipping reminder evaluation")
        return 0

    try:
        kimai_users = async_to_sync(client.get_users)()
    except Exception:
        logger.exception("Failed to fetch Kimai users for reminder evaluation")
        return 0

    kimai_by_id: dict[int, dict] = {u["id"]: u for u in kimai_users}

    today = datetime.now(tz=UTC).date()
    holidays = get_public_holidays(settings.holiday_country, today.year)

    django_users = User.objects.filter(is_active=True).values("pk", "email")
    evaluated = 0

    for user_row in django_users:
        email = (user_row["email"] or "").lower()

        # V9: exempt check first
        if email in exempt:
            continue

        kimai_uid = email_map.get(email)
        if not kimai_uid:
            # V8: skip, log warning
            logger.debug("No Kimai user for email %s — skipping reminder", email)
            continue

        kimai_user = kimai_by_id.get(kimai_uid)
        if not kimai_user:
            continue

        working_days = parse_working_days(kimai_user.get("accountNumber") or "")
        if not working_days:
            # V7: skip, no error
            continue

        try:
            last_ts = async_to_sync(client.get_last_timesheet)(kimai_uid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to fetch last timesheet for Kimai user %d", kimai_uid
            )
            continue

        last_entry_end: datetime | None = None
        if last_ts:
            raw_end = last_ts.get("end")
            if raw_end:
                parsed = parse_datetime(str(raw_end).replace("Z", "+00:00"))
                if parsed:
                    last_entry_end = (
                        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
                    )

        days_behind = calc_days_behind(last_entry_end, working_days, holidays)

        cache_key = f"kimai_reminder:{user_row['pk']}"
        cache.set(
            cache_key,
            {
                "days_behind": days_behind,
                "ts": datetime.now(tz=UTC).isoformat(),
            },
            timeout=CACHE_TTL_REMINDER,
        )
        evaluated += 1

    logger.info("Reminder evaluation complete: %d users processed.", evaluated)
    return evaluated
