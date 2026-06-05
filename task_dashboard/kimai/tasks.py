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
import secrets
from datetime import UTC
from datetime import datetime
from typing import Any

import httpx
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.core.mail import send_mail
from django.template import Context
from django.template import Template
from django.utils.dateparse import parse_datetime
from django.utils.html import linebreaks

from task_dashboard.users.models import EmailConfiguration
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskOwner

from .client import KimaiClient
from .holidays import get_public_holidays
from .models import DEFAULT_REMINDER_EMAIL_BODY
from .models import DEFAULT_REMINDER_EMAIL_SUBJECT
from .models import KimaiSettings
from .reminder import calc_days_behind

logger = logging.getLogger(__name__)

CACHE_TTL_EMAIL_MAP = 86400  # 24h
CACHE_TTL_REMINDER = 3600  # 1h
CACHE_TTL_EMAILED = 82800  # 23h — one reminder email per owner per day
_MAX_WEEKDAY = 6

KIMAI_ACTIVITY_COMMENT_SEP = ":"
# Per-config sync lock. Kimai has no unique constraint on customer/project
# names, so two overlapping syncs of the same service each create their own
# copies (observed: duplicated customers). cache.add is atomic on the Valkey
# backend, so it acts as a cross-worker lock. TTL is a safety release in case a
# worker dies mid-sync (a full run is ~15 min, the schedule interval is 15 min).
_SYNC_LOCK_PREFIX = "kimai_sync_lock:"
_SYNC_LOCK_TTL = 1800  # 30 min
# Marker prefix for the 1-member per-owner teams we manage (item 6). Used both
# to name new teams readably and to recognise which teams we may auto-revoke.
_OWNER_TEAM_PREFIX = "Owner: "


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

    # Skip if another run for this service is already in progress (prevents the
    # duplicate-customer race). cache.add returns False when the key exists.
    lock_key = f"{_SYNC_LOCK_PREFIX}{config_id}"
    if not cache.add(lock_key, 1, timeout=_SYNC_LOCK_TTL):
        logger.info(
            "Kimai sync for config %d (%s) already running — skipping this run.",
            config_id,
            config.name,
        )
        return 0
    try:
        return async_to_sync(_sync_activities_async)(client, config, settings)
    finally:
        cache.delete(lock_key)


def _split_owner_emails(raw: str | None) -> list[str]:
    """Split a (possibly comma-separated) owner_email field into clean emails."""
    if not raw:
        return []
    return [e.strip().lower() for e in raw.split(",") if e.strip() and "@" in e]


def _norm_customer_key(name: str | None) -> str:
    """Case/whitespace-insensitive customer key.

    Names that differ only in case or surrounding whitespace (e.g. 'nGENn GmbH'
    from Zammad vs 'nGENn Gmbh' from OpenProject) should map to a single Kimai
    customer rather than creating duplicates.
    """
    return (name or "").strip().casefold()


async def _sync_activities_async(  # noqa: C901, PLR0915
    client: KimaiClient, config: ServiceConfiguration, settings: KimaiSettings
) -> int:
    # Fetch global settings and Kimai customers/projects/users/teams once
    global_settings = await GlobalSetting.objects.afirst()

    try:
        customers = await client.get_customers()
        all_projects = await client.get_projects()
        kimai_users = await client.get_users()
        existing_teams = await client.get_teams()
    except Exception:
        logger.exception(
            "Failed to fetch Kimai customers/projects for config %s", config.name
        )
        return 0

    customer_by_name: dict[str, dict] = {
        _norm_customer_key(c["name"]): c for c in customers
    }
    project_by_comment: dict[str, dict] = {
        p["comment"]: p for p in all_projects if p.get("comment")
    }

    # email -> kimai user id (membership). Teams are keyed by owner email so a
    # team exists for every owner, including discovered ones with no Kimai
    # account yet (the activity is still restricted; membership is added once
    # they get an account).
    email_map: dict[str, int] = {}
    for u in kimai_users:
        email = (u.get("email") or u.get("title") or "").strip().lower()
        uid = u.get("id")
        if email and uid:
            email_map[email] = uid

    # Per-owner team. Name = "Owner: {email}" — readable + unique + stable lookup.
    def _owner_team_name(email: str) -> str:
        return f"{_OWNER_TEAM_PREFIX}{email}"

    team_name_to_id: dict[str, int] = {
        t["name"]: t["id"] for t in existing_teams if t.get("name")
    }
    team_by_email: dict[str, int | None] = {}
    team_lock = asyncio.Lock()
    owners_without_kimai_account: set[str] = set()
    created_kimai_users: set[str] = set()
    kimai_timezone = (
        global_settings.kimai_customer_timezone if global_settings else "Europe/Berlin"
    )

    async def provision_kimai_user(email: str) -> int | None:
        """Create a Kimai user for a discovered owner so they can have a team.

        Kimai requires a password on creation; a random one is set (the user logs
        in via SSO / password reset, never this password). Returns the new uid.
        """
        try:
            user = await client.create_user(
                {
                    "username": email,
                    "alias": email,
                    "email": email,
                    "language": "en",
                    "timezone": kimai_timezone,
                    "enabled": True,
                    "plainPassword": secrets.token_urlsafe(24),
                }
            )
            uid = user["id"]
            email_map[email] = uid
            created_kimai_users.add(email)
        except Exception:
            logger.exception("Failed to create Kimai user for %s", email)
            return None
        else:
            return uid

    # Ids of teams we manage (one per owner) — only these may be auto-revoked,
    # so manually-assigned teams on an activity are never touched.
    owner_team_ids: set[int] = {
        tid
        for name, tid in team_name_to_id.items()
        if name.startswith(_OWNER_TEAM_PREFIX)
    }

    async def ensure_owner_team(email: str) -> int | None:
        """Return the id of the 1-member team for an owner email, creating it once.

        Named by email and restricted to that owner. Kimai rejects memberless
        teams, so a team can only be created once the owner has a Kimai account;
        owners without one are tracked and their activities are left without a
        per-user team (see the summary log).
        """
        if email in team_by_email:
            return team_by_email[email]
        async with team_lock:
            if email in team_by_email:
                return team_by_email[email]
            uid = email_map.get(email)
            name = _owner_team_name(email)
            tid = team_name_to_id.get(name)
            if tid is None:
                if uid is None:
                    # No Kimai account → auto-provision one (Kimai rejects
                    # memberless teams, so a member is required).
                    uid = await provision_kimai_user(email)
                    if uid is None:
                        owners_without_kimai_account.add(email)
                        team_by_email[email] = None
                        return None
                try:
                    team = await client.create_team(
                        {"name": name, "members": [{"user": uid, "teamlead": True}]}
                    )
                    tid = team["id"]
                    team_name_to_id[name] = tid
                    owner_team_ids.add(tid)
                except Exception:
                    logger.exception("Failed to create Kimai team %r", name)
                    tid = None
            elif uid:
                # Pre-existing team — make sure the owner is actually a member.
                try:
                    await client.add_team_member(tid, uid)
                except httpx.HTTPError:
                    logger.debug(
                        "add_team_member(%d, %d) failed (likely already a member)",
                        tid,
                        uid,
                    )
            team_by_email[email] = tid
            return tid

    # Group tasks by (ExternalGroup name, customer) so a project never spans
    # two customers — the customer is the Zammad organization (item 2).
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
                "owner_email",
            )
        )
    )

    fallback_company = global_settings.company_name if global_settings else config.name

    def _customer_for(t: dict) -> str:
        if config.kimai_customer_name:
            return config.kimai_customer_name
        return t.get("customer") or fallback_company

    by_group: dict[tuple[str, str], list[dict]] = {}
    for t in tasks_qs:
        group_name = t.get("service_group__name") or config.name
        by_group.setdefault((group_name, _customer_for(t)), []).append(t)

    # Phase 1: resolve project IDs sequentially (avoids duplicate create races)
    resolved: list[tuple[int, list[dict]]] = []  # (project_id, group_tasks)
    for (group_name, customer_name), group_tasks in by_group.items():
        project_comment_key = f"{config.name}::{group_name}::{customer_name}"
        kimai_project = project_by_comment.get(project_comment_key)
        if not kimai_project:
            kimai_customer = customer_by_name.get(_norm_customer_key(customer_name))
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
                    customer_by_name[_norm_customer_key(customer_name)] = kimai_customer
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

    async def _grant_owner_teams(
        activity_id: int, task_data: dict, *, is_new: bool
    ) -> None:
        """Restrict an activity to its owners' 1-member teams (item 6).

        A team is ensured for every owner email (incl. discovered ones with no
        Kimai account). Kimai allows multiple teams per activity, so a multi-owner
        task is granted to each owner's team. When ownership changes, owner teams
        we manage that are no longer owners are revoked (access control);
        manually-assigned teams are never touched.
        """
        emails = _split_owner_emails(task_data.get("owner_email"))
        if not emails:
            return
        desired: set[int] = set()
        for email in emails:
            tid = await ensure_owner_team(email)
            if tid:
                desired.add(tid)

        # Read the activity's current teams from its detail (the list endpoint
        # does not include them). Newly created activities have none.
        current_tids: set[int] = set()
        if not is_new:
            try:
                detail = await client.get_activity(activity_id)
                current_tids = {
                    tm.get("id") for tm in detail.get("teams", []) if tm.get("id")
                }
            except Exception:
                logger.exception("Failed to read teams for activity %d", activity_id)

        for tid in desired - current_tids:
            try:
                await client.grant_team_activity(tid, activity_id)
            except Exception:
                logger.exception(
                    "Failed to grant team %d to activity %d", tid, activity_id
                )
        # Revoke only owner teams we manage that are no longer owners.
        for tid in (current_tids & owner_team_ids) - desired:
            try:
                await client.revoke_team_activity(tid, activity_id)
            except Exception:
                logger.exception(
                    "Failed to revoke team %d from activity %d", tid, activity_id
                )

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
                activity_id = existing["id"] if existing else None
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
                        created = await client.create_activity(
                            {
                                "name": title,
                                "project": project_id,
                                "comment": comment,
                                "visible": True,
                            }
                        )
                        activity_id = created["id"]
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

                # Restrict visibility to the owner(s) (item 6).
                if activity_id:
                    await _grant_owner_teams(
                        activity_id, task_data, is_new=existing is None
                    )
            return count

    counts = await asyncio.gather(*(_sync_project(pid, gt) for pid, gt in resolved))
    if created_kimai_users:
        logger.info(
            "Auto-created %d Kimai user(s) for config %s: %s",
            len(created_kimai_users),
            config.name,
            ", ".join(sorted(created_kimai_users)),
        )
    if owners_without_kimai_account:
        logger.warning(
            "%d owner(s) could not be provisioned in Kimai for config %s — their "
            "activities remain globally visible: %s",
            len(owners_without_kimai_account),
            config.name,
            ", ".join(sorted(owners_without_kimai_account)),
        )
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
    For every task owner with a matching Kimai account, compute days_behind and
    write to kimai_reminder:owner:{owner_pk} Valkey cache (and the legacy
    kimai_reminder:{user_pk} key for linked users so the banner keeps working).
    Fetches all last timesheets concurrently (parallel HTTP calls).
    Returns count of owners evaluated.
    """
    settings = KimaiSettings.load()
    if not settings.reminder_enabled:
        return 0

    client = _get_kimai_client()
    if not client:
        return 0

    return async_to_sync(_run_reminder_evaluation_async)(client, settings)


async def _run_reminder_evaluation_async(  # noqa: C901, PLR0912, PLR0915
    client: KimaiClient, settings: KimaiSettings
) -> int:
    exempt = await asyncio.to_thread(settings.get_exempt_email_set)

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

    owners = await asyncio.to_thread(
        lambda: list(
            TaskOwner.objects.values(
                "pk", "email", "user_id", "user__working_days", "kimai_user_id"
            )
        )
    )

    # Build list of (owner_row, kimai_uid, working_days) for owners to evaluate
    to_evaluate: list[tuple[Any, int, frozenset[int]]] = []
    uid_updates: list[tuple[int, int]] = []  # (owner_pk, kimai_uid)
    for owner_row in owners:
        email = (owner_row["email"] or "").lower()
        if not email or email in exempt:
            continue
        kimai_uid = email_map.get(email)
        if not kimai_uid:
            logger.debug("No Kimai user for owner %s — skipping reminder", email)
            continue
        if owner_row.get("kimai_user_id") != kimai_uid:
            uid_updates.append((owner_row["pk"], kimai_uid))
        raw_days = owner_row.get("user__working_days") or [0, 1, 2, 3, 4]
        working_days = frozenset(
            int(d) for d in raw_days if 0 <= int(d) <= _MAX_WEEKDAY
        )
        if not working_days:
            continue
        to_evaluate.append((owner_row, kimai_uid, working_days))

    # Persist resolved Kimai user ids so the overview can link without a cache hit.
    if uid_updates:

        def _persist_uids() -> None:
            for owner_pk, uid in uid_updates:
                TaskOwner.objects.filter(pk=owner_pk).update(kimai_user_id=uid)

        await asyncio.to_thread(_persist_uids)

    if not to_evaluate:
        return 0

    # Fetch all last timesheets concurrently
    uid_to_ts = await client.get_last_timesheets_bulk(
        [uid for _, uid, _ in to_evaluate]
    )

    evaluated = 0
    cache_data: dict[str, dict] = {}

    for owner_row, kimai_uid, working_days in to_evaluate:
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
        payload = {
            "days_behind": days_behind,
            "never_booked": never_booked,
            "ts": datetime.now(tz=UTC).isoformat(),
        }
        cache_data[f"kimai_reminder:owner:{owner_row['pk']}"] = payload
        # Legacy per-user key powers the request-time banner / context processor.
        if owner_row.get("user_id"):
            cache_data[f"kimai_reminder:{owner_row['user_id']}"] = payload
        evaluated += 1

    # Write all cache entries
    def _write_cache() -> None:
        for k, v in cache_data.items():
            cache.set(k, v, timeout=CACHE_TTL_REMINDER)

    await asyncio.to_thread(_write_cache)

    logger.info("Reminder evaluation complete: %d owners processed.", evaluated)
    return evaluated


# ---------------------------------------------------------------------------
# T13 — send_kimai_reminder_emails (daily digest)
# ---------------------------------------------------------------------------


def _kimai_base_url() -> str:
    """Active Kimai base URL (no trailing slash), or "" if none."""
    config = ServiceConfiguration.objects.filter(
        service_type="kimai", is_active=True
    ).first()
    return config.api_url.rstrip("/") if config and config.api_url else ""


def _render_template_string(source: str, context: dict) -> str:
    """Render an admin-edited template string with the reminder context."""
    return Template(source).render(Context(context))


def _build_reminder_email(settings, context: dict) -> tuple[str, str, str]:
    """Render (subject, text_body, html_body) from the admin-editable fields."""
    subject_tpl = (
        settings.reminder_email_subject.strip() or DEFAULT_REMINDER_EMAIL_SUBJECT
    )
    body_tpl = settings.reminder_email_body.strip() or DEFAULT_REMINDER_EMAIL_BODY
    subject = _render_template_string(subject_tpl, context).strip()
    text_body = _render_template_string(body_tpl, context)
    html_body = linebreaks(text_body)
    return subject, text_body, html_body


def send_kimai_reminder_emails() -> int:
    """
    Email every owner who is currently behind on time tracking (T13).

    Reads the per-owner reminder cache the evaluation job already wrote — no
    Kimai API calls. Runs once per day (CRON schedule), so each owner gets at
    most one mail/day. Returns the count of emails sent.
    """
    settings = KimaiSettings.load()
    if not (settings.reminder_enabled and settings.reminder_email_enabled):
        return 0

    grace = settings.grace_period_days
    exempt = settings.get_exempt_email_set()
    owners = list(TaskOwner.objects.values("pk", "email", "name"))
    if not owners:
        return 0

    keys = {o["pk"]: f"kimai_reminder:owner:{o['pk']}" for o in owners}
    cached = cache.get_many(list(keys.values()))
    kimai_url = _kimai_base_url()
    today = datetime.now(tz=UTC).date().isoformat()

    email_conf = EmailConfiguration.load()
    connection = email_conf.get_connection()
    from_email = email_conf.get_from_email()

    sent = 0
    for o in owners:
        email = (o["email"] or "").strip()
        if not email or email.lower() in exempt:
            continue
        data = cached.get(keys[o["pk"]])
        if not data:
            continue
        never_booked = bool(data.get("never_booked"))
        days_behind = int(data.get("days_behind", 0))
        if not (never_booked or days_behind > grace):
            continue

        # At most one email per owner per day — guards manual re-runs / restarts
        # / double-fires on top of the daily schedule.
        sent_key = f"kimai_reminder_emailed:{o['pk']}"
        if cache.get(sent_key) == today:
            continue

        context = {
            "name": o["name"] or email,
            "days_behind": days_behind,
            "never_booked": never_booked,
            "grace_period": grace,
            "kimai_url": kimai_url,
        }
        subject, text_body, html_body = _build_reminder_email(settings, context)
        try:
            send_mail(
                subject,
                text_body,
                from_email,
                [email],
                html_message=html_body,
                connection=connection,
            )
            cache.set(sent_key, today, timeout=CACHE_TTL_EMAILED)
            sent += 1
        except Exception:
            logger.exception("Failed to send reminder email to %s", email)

    logger.info("Reminder emails sent: %d", sent)
    return sent
