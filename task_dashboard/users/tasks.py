import hashlib
import logging
from collections.abc import Callable
from typing import Any
from typing import Protocol
from typing import runtime_checkable

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime
from django_q.tasks import async_task

from task_dashboard.kimai.service import KimaiService
from task_dashboard.services.eramba import ErambaService
from task_dashboard.services.espocrm import EspoService
from task_dashboard.services.gitlab import GitLabService
from task_dashboard.services.openproject import OpenProjectService
from task_dashboard.services.zammad import ZammadService

from .models import ExternalGroup
from .models import ServiceConfiguration
from .models import Task
from .models import TaskOwner
from .models import User

logger = logging.getLogger(__name__)


@runtime_checkable
class TaskService(Protocol):
    def get_tasks(self, *, force_refresh: bool = False) -> list[dict[str, Any]]: ...

    def get_single_task(self, task: Task) -> dict[str, Any] | None: ...


SERVICE_CLASSES: dict[str, Callable[..., TaskService]] = {
    "zammad": ZammadService,
    "gitlab": GitLabService,
    "espocrm": EspoService,
    "openproject": OpenProjectService,
    "eramba": ErambaService,
    "kimai": KimaiService,
}


def parse_dt(dt_str):
    """Helper to parse datetime strings and ensure they are timezone-aware."""
    if not dt_str:
        return None
    dt = parse_datetime(dt_str)
    if dt and django_timezone.is_naive(dt):
        return django_timezone.make_aware(dt)
    return dt


def _prepare_upsert_data(config, tasks_data, group_map=None):
    """Helper to prepare task and group objects for batch upsert."""
    tasks_to_upsert = {}
    groups_to_upsert = {}

    for task_dict in tasks_data:
        task_id = task_dict["id"]
        group_name = task_dict.get("group") or ""

        # Link to ExternalGroup if map is provided
        service_group = None
        if group_map and group_name in group_map:
            service_group = group_map[group_name]

        tasks_to_upsert[task_id] = Task(
            service=config,
            external_id=task_id,
            title=task_dict.get("title") or "",
            status=task_dict.get("status") or "",
            priority=task_dict.get("priority") or "",
            original_status=task_dict.get("original_status") or "",
            original_priority=task_dict.get("original_priority") or "",
            customer=task_dict.get("customer") or "",
            group=group_name,
            service_group=service_group,
            owner=task_dict.get("owner") or "",
            owner_email=task_dict.get("owner_email") or "",
            url=task_dict.get("url") or "",
            created_at=parse_dt(task_dict.get("created_at")),
            updated_at=parse_dt(task_dict.get("updated_at")),
            due_date=parse_dt(task_dict.get("due_date")),
        )

        if group_name:
            groups_to_upsert[(config.name, group_name)] = ExternalGroup(
                origin=config.name,
                name=group_name,
                extra_data=task_dict.get("extra_info") or {},
            )
    return tasks_to_upsert, groups_to_upsert


def _iter_owner_pairs(tasks_data):
    """Yield (email, name) for every distinct owner across all tasks.

    ``owner_email`` / ``owner`` may be comma-separated (multi-owner tasks); names
    are aligned with emails by position when the two lists match in length.
    """
    seen: dict[str, str] = {}
    for td in tasks_data:
        emails = [e.strip().lower() for e in (td.get("owner_email") or "").split(",")]
        emails = [e for e in emails if e and "@" in e]
        names = [n.strip() for n in (td.get("owner") or "").split(",")]
        for idx, email in enumerate(emails):
            name = names[idx] if len(names) == len(emails) else ""
            # First non-empty name wins; never downgrade a known name to "".
            if email not in seen or (name and not seen[email]):
                seen[email] = name
    yield from seen.items()


def upsert_task_owners(tasks_data) -> int:
    """Create/refresh TaskOwner rows from synced tasks; link to Django users.

    Never deletes owners (independent lifetime — only admin removes them).
    Returns the number of owner emails processed.
    """
    pairs = list(_iter_owner_pairs(tasks_data))
    if not pairs:
        return 0

    now = django_timezone.now()
    TaskOwner.objects.bulk_create(
        [TaskOwner(email=email, name=name, last_seen=now) for email, name in pairs],
        batch_size=500,
        update_conflicts=True,
        unique_fields=["email"],
        update_fields=["name", "last_seen"],
    )

    # Auto-link owners to existing Django users by email (where not yet linked).
    emails = [email for email, _ in pairs]
    user_by_email = {u.email.lower(): u for u in User.objects.filter(email__in=emails)}
    to_link = []
    for owner in TaskOwner.objects.filter(email__in=emails, user__isnull=True):
        user = user_by_email.get(owner.email.lower())
        if user:
            owner.user = user
            to_link.append(owner)
    if to_link:
        TaskOwner.objects.bulk_update(to_link, ["user"], batch_size=500)

    return len(pairs)


def _get_task_hash(task_dict: dict[str, Any]) -> str:
    """Generates a stable hash of relevant task fields to detect changes."""
    relevant_fields = [
        task_dict.get("title") or "",
        task_dict.get("status") or "",
        task_dict.get("priority") or "",
        task_dict.get("customer") or "",
        task_dict.get("group") or "",
        task_dict.get("owner") or "",
        task_dict.get("owner_email") or "",
        task_dict.get("url") or "",
        str(task_dict.get("updated_at") or ""),
        str(task_dict.get("due_date") or ""),
    ]
    return hashlib.sha256("|".join(relevant_fields).encode("utf-8")).hexdigest()


def fetch_service_tasks(config_id: int):  # noqa: C901
    """
    Fetches tasks for a specific service configuration and performs batch upsert.
    """
    try:
        config = ServiceConfiguration.objects.get(pk=config_id, is_active=True)
    except ServiceConfiguration.DoesNotExist:
        logger.exception(
            "ServiceConfiguration with id %s not found or inactive.", config_id
        )
        return 0

    service_class = SERVICE_CLASSES.get(config.service_type)
    if not service_class:
        logger.error(
            "Unknown service type '%s' for configuration '%s'",
            config.service_type,
            config.name,
        )
        return 0

    logger.info("Fetching tasks for service: %s (%s)", config.name, config.service_type)
    service_instance = service_class(config)
    try:
        tasks_data = service_instance.get_tasks(force_refresh=True)
    except Exception:
        logger.exception("Error fetching tasks for service %s", config.name)
        return 0

    # PRE-UPSERT OPTIMIZATION: Pull existing IDs and update timestamps
    # to skip unchanged tasks
    existing_tasks = dict(
        Task.objects.filter(service=config).values_list("external_id", "updated_at")
    )

    filtered_tasks_data = []
    for td in tasks_data:
        ext_id = td["id"]
        # Simple change detection: if updated_at is newer or doesn't exist, we upsert.
        if ext_id not in existing_tasks:
            filtered_tasks_data.append(td)
        else:
            # Check if external update timestamp is newer than our local one
            ext_updated = parse_dt(td.get("updated_at"))
            loc_updated = existing_tasks[ext_id]
            if not loc_updated or (ext_updated and ext_updated > loc_updated):
                filtered_tasks_data.append(td)

    _, groups_to_upsert = _prepare_upsert_data(config, tasks_data)

    # Perform Batch Upserts and Pruning in a single transaction
    try:
        with transaction.atomic():
            if groups_to_upsert:
                ExternalGroup.objects.bulk_create(
                    groups_to_upsert.values(),
                    batch_size=500,
                    update_conflicts=True,
                    unique_fields=["origin", "name"],
                    update_fields=["extra_data", "last_seen"],
                )

            # Map the ExternalGroup objects to names for linking
            group_map = {
                g.name: g for g in ExternalGroup.objects.filter(origin=config.name)
            }

            # Prepare Task objects with service_group linked (only for changed ones)
            tasks_to_upsert, _ = _prepare_upsert_data(
                config, filtered_tasks_data, group_map=group_map
            )

            if tasks_to_upsert:
                Task.objects.bulk_create(
                    tasks_to_upsert.values(),
                    batch_size=500,
                    update_conflicts=True,
                    unique_fields=["service", "external_id"],
                    update_fields=[
                        "title",
                        "status",
                        "priority",
                        "original_status",
                        "original_priority",
                        "customer",
                        "group",
                        "service_group",
                        "owner",
                        "owner_email",
                        "url",
                        "created_at",
                        "updated_at",
                        "due_date",
                    ],
                )

            # PRUNING: Remove tasks that are no longer in the service
            # (must use full tasks_data set for pruning)
            active_ids = {t["id"] for t in tasks_data}
            deleted_count, _ = (
                Task.objects.filter(service=config)
                .exclude(external_id__in=active_ids)
                .delete()
            )
            if deleted_count:
                logger.info("Pruned %s stale tasks for %s", deleted_count, config.name)

            # Signal that the sync for this service completed successfully
            cache.set("last_task_sync", django_timezone.now())

    except Exception:
        logger.exception("Database error while syncing tasks for %s", config.name)
        return 0

    # Owner records have an independent lifetime: upsert from the full task set,
    # never pruned with tasks. Failures here must not fail the task sync.
    try:
        upsert_task_owners(tasks_data)
    except Exception:
        logger.exception("Failed to upsert task owners for %s", config.name)

    logger.info(
        "Successfully processed %s tasks (upserted %s) for %s",
        len(tasks_data),
        len(tasks_to_upsert),
        config.name,
    )
    return len(tasks_data)


def fetch_all_tasks_task():
    """
    Main task to trigger task fetching for all active services.
    Dispatches individual service fetches in parallel.
    """
    active_configs = ServiceConfiguration.objects.filter(is_active=True)
    for config in active_configs:
        logger.info("Dispatching parallel fetch for service: %s", config.name)
        async_task("task_dashboard.users.tasks.fetch_service_tasks", config.id)

    return active_configs.count()
