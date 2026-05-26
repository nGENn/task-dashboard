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
from typing import Any

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.utils.dateparse import parse_datetime

from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.models import User

from .client import KimaiClient
from .holidays import get_public_holidays
from .models import KimaiSettings
from .reminder import calc_days_behind

logger = logging.getLogger(__name__)

CACHE_TTL_EMAIL_MAP = 86400  # 24h
CACHE_TTL_REMINDER = 3600  # 1h
_MAX_WEEKDAY = 6

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
        email = (u.get("email") or u.get("title") or "").strip().lower()
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


async def _sync_activities_async(  # noqa: C901, PLR0915
    client: KimaiClient, config: ServiceConfiguration, settings: KimaiSettings
) -> int:
    # Fetch global settings and Kimai customers/projects once
    global_settings = await GlobalSetting.objects.afirst()

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

    fallback_company = global_settings.company_name if global_settings else config.name

    # Phase 1: resolve project IDs sequentially (avoids duplicate create races)
    resolved: list[tuple[int, list[dict]]] = []  # (project_id, group_tasks)
    for group_name, group_tasks in by_group.items():
        project_comment_key = f"{config.name}::{group_name}"
        kimai_project = project_by_comment.get(project_comment_key)
        if not kimai_project:
            # Determine customer: service override → task customer → global company name
            if config.kimai_customer_name:
                customer_name = config.kimai_customer_name
            else:
                customer_name = group_tasks[0].get("customer") or fallback_company
            kimai_customer = customer_by_name.get(customer_name)
            if not kimai_customer:
                try:
                    kimai_customer = await client.create_customer(
                        {
                            "name": customer_name,
                            "visible": True,
                            "currency": "EUR",
                            "country": global_settings.kimai_customer_country
                            if global_settings
                            else "DE",
                            "timezone": global_settings.kimai_customer_timezone
                            if global_settings
                            else "Europe/Berlin",
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

        resolved.append((kimai_project["id"], group_tasks))

    # Phase 2: sync activities for each project concurrently (capped at 5 in parallel).
    sem = asyncio.Semaphore(5)

    async def _sync_project(project_id: int, group_tasks: list[dict]) -> int:  # noqa: C901, PLR0912
        async with sem:
            try:
                existing_activities = await client.get_activities(project_id)
            except Exception:
                logger.exception(
                    "Failed to fetch activities for project %d", project_id
                )
                return 0

            activity_by_comment: dict[str, dict] = {}
            for act in existing_activities:
                parsed = _parse_activity_comment(act.get("comment"))
                if parsed and parsed[0] == config.id:
                    activity_by_comment[act["comment"]] = act

            current_comments = {
                _activity_comment(config.id, t["external_id"]): t for t in group_tasks
            }

            count = 0
            # Hide activities whose source task no longer exists / is closed (V12, C13)
            for comment, act in activity_by_comment.items():
                task_data = current_comments.get(comment)
                if task_data is None or task_data.get("status") == "closed":
                    if act.get("visible") is not False:
                        try:
                            await client.patch_activity(act["id"], {"visible": False})
                        except Exception:
                            logger.exception("Failed to hide activity %d", act["id"])
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
                raw_title = task_data.get("title") or task_data["external_id"]
                title = raw_title.translate(str.maketrans('<>"=', "()'-"))
                if existing:
                    if existing.get("name") != title:
                        try:
                            await client.patch_activity(
                                existing["id"], {"name": title, "project": project_id}
                            )
                        except httpx.HTTPStatusError as exc:
                            logger.exception(
                                "Failed to update activity name for %s: %s %s",
                                comment,
                                exc.response.status_code,
                                exc.response.text,
                            )
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
                        count += 1
                    except httpx.HTTPStatusError as exc:
                        logger.exception(
                            "Failed to create activity for task %s: %s %s",
                            comment,
                            exc.response.status_code,
                            exc.response.text,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to create activity for task %s", comment
                        )
            return count

    counts = await asyncio.gather(*(_sync_project(pid, gt) for pid, gt in resolved))
    return sum(counts)


def sync_kimai_activities() -> int:
    """Fan-out: sync activities for every active non-kimai service."""
    from django_q.tasks import async_task

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


def run_reminder_evaluation() -> int:
    """
    For every Django user with a matching Kimai account, compute days_behind
    and write to kimai_reminder:{user_pk} Valkey cache (V5, V6, V9).
    Fetches all last timesheets concurrently (parallel HTTP calls).
    Returns count of users evaluated.
    """
    settings = KimaiSettings.load()
    if not settings.reminder_enabled:
        return 0

    client = _get_kimai_client()
    if not client:
        return 0

    return async_to_sync(_run_reminder_evaluation_async)(client, settings)


async def _run_reminder_evaluation_async(  # noqa: C901, PLR0912
    client: KimaiClient, settings: KimaiSettings
) -> int:
    exempt = settings.get_exempt_email_set()

    try:
        kimai_users = await client.get_users()
    except Exception:
        logger.exception("Failed to fetch Kimai users for reminder evaluation")
        return 0

    # Build and refresh the email map from the already-fetched user list.
    # Avoids a sync get_kimai_email_map() call inside an async context (re-entry risk).
    email_map: dict[str, int] = {}
    for u in kimai_users:
        email = (u.get("email") or u.get("title") or "").strip().lower()
        uid = u.get("id")
        if email and uid:
            email_map[email] = uid
    await asyncio.to_thread(
        lambda: cache.set("kimai_email_map", email_map, timeout=CACHE_TTL_EMAIL_MAP)
    )

    if not email_map:
        logger.warning("kimai_email_map empty — skipping reminder evaluation")
        return 0

    today = datetime.now(tz=UTC).date()
    holidays = get_public_holidays(settings.holiday_country, today.year)

    django_users = await asyncio.to_thread(
        lambda: list(
            User.objects.filter(is_active=True).values("pk", "email", "working_days")
        )
    )

    # Build list of (user_row, kimai_uid, working_days) for users that need evaluation
    to_evaluate: list[tuple[Any, int, frozenset[int]]] = []
    for user_row in django_users:
        email = (user_row["email"] or "").lower()
        if email in exempt:
            continue
        kimai_uid = email_map.get(email)
        if not kimai_uid:
            logger.debug("No Kimai user for email %s — skipping reminder", email)
            continue
        raw_days = user_row.get("working_days") or [0, 1, 2, 3, 4]
        working_days = frozenset(
            int(d) for d in raw_days if 0 <= int(d) <= _MAX_WEEKDAY
        )
        if not working_days:
            continue
        to_evaluate.append((user_row, kimai_uid, working_days))

    if not to_evaluate:
        return 0

    # Fetch all last timesheets concurrently
    uid_to_ts = await client.get_last_timesheets_bulk(
        [uid for _, uid, _ in to_evaluate]
    )

    evaluated = 0
    cache_data: dict[str, dict] = {}

    for user_row, kimai_uid, working_days in to_evaluate:
        last_ts = uid_to_ts.get(kimai_uid)
        never_booked = last_ts is None
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
        cache_data[f"kimai_reminder:{user_row['pk']}"] = {
            "days_behind": days_behind,
            "never_booked": never_booked,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
        evaluated += 1

    # Write all cache entries
    def _write_cache() -> None:
        for k, v in cache_data.items():
            cache.set(k, v, timeout=CACHE_TTL_REMINDER)

    await asyncio.to_thread(_write_cache)

    logger.info("Reminder evaluation complete: %d users processed.", evaluated)
    return evaluated
