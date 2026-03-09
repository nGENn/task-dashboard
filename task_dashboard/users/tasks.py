import logging

from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime
from django_q.tasks import async_task

from task_dashboard.services.eramba import ErambaService
from task_dashboard.services.espocrm import EspoService
from task_dashboard.services.gitlab import GitLabService
from task_dashboard.services.openproject import OpenProjectService
from task_dashboard.services.zammad import ZammadService

from .models import ExternalGroup
from .models import ServiceConfiguration
from .models import Task

logger = logging.getLogger(__name__)

SERVICE_CLASSES = {
    "zammad": ZammadService,
    "gitlab": GitLabService,
    "espocrm": EspoService,
    "openproject": OpenProjectService,
    "eramba": ErambaService,
}


def parse_dt(dt_str):
    """Helper to parse datetime strings and ensure they are timezone-aware."""
    if not dt_str:
        return None
    dt = parse_datetime(dt_str)
    if dt and django_timezone.is_naive(dt):
        return django_timezone.make_aware(dt)
    return dt


def _prepare_upsert_data(config, tasks_data):
    """Helper to prepare task and group objects for batch upsert."""
    tasks_to_upsert = {}
    groups_to_upsert = {}

    for task_dict in tasks_data:
        task_id = task_dict["id"]
        tasks_to_upsert[task_id] = Task(
            service=config,
            external_id=task_id,
            title=task_dict.get("title", ""),
            status=task_dict.get("status", ""),
            priority=task_dict.get("priority", ""),
            customer=task_dict.get("customer") or "",
            group=task_dict.get("group", ""),
            owner=task_dict.get("owner", ""),
            owner_email=task_dict.get("owner_email", "") or "",
            url=task_dict.get("url", ""),
            created_at=parse_dt(task_dict.get("created_at")),
            updated_at=parse_dt(task_dict.get("updated_at")),
            due_date=parse_dt(task_dict.get("due_date")),
        )

        group_name = task_dict.get("group")
        if group_name:
            groups_to_upsert[(config.name, group_name)] = ExternalGroup(
                origin=config.name,
                name=group_name,
                extra_data=task_dict.get("extra_info", {}),
            )
    return tasks_to_upsert, groups_to_upsert


def fetch_service_tasks(config_id: int):
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

    tasks_to_upsert, groups_to_upsert = _prepare_upsert_data(config, tasks_data)

    # Perform Batch Upserts
    if groups_to_upsert:
        ExternalGroup.objects.bulk_create(
            groups_to_upsert.values(),
            update_conflicts=True,
            unique_fields=["origin", "name"],
            update_fields=["extra_data", "last_seen"],
        )

    if tasks_to_upsert:
        Task.objects.bulk_create(
            tasks_to_upsert.values(),
            update_conflicts=True,
            unique_fields=["service", "external_id"],
            update_fields=[
                "title",
                "status",
                "priority",
                "customer",
                "group",
                "owner",
                "owner_email",
                "url",
                "created_at",
                "updated_at",
                "due_date",
            ],
        )

        # PRUNING: Remove tasks that are no longer in the service
        deleted_count, _ = (
            Task.objects.filter(service=config)
            .exclude(
                external_id__in=tasks_to_upsert.keys(),
            )
            .delete()
        )
        if deleted_count:
            logger.info("Pruned %s stale tasks for %s", deleted_count, config.name)
    elif tasks_data:
        # If we got tasks back but they were all invalid or filtered out,
        # but the request itself SUCCEEDED (non-empty tasks_data list),
        # we should still prune existing tasks.
        deleted_count, _ = Task.objects.filter(service=config).delete()
        if deleted_count:
            logger.info("Pruned %s stale tasks for %s", deleted_count, config.name)
    else:
        # Request returned 0 tasks or was empty.
        # SAFEGUARD: To avoid fluctuation, only prune if we are sure the service
        # returned a valid empty response, not an error.
        logger.warning(
            "Service %s returned 0 results. Skipping pruning to prevent fluctuation.",
            config.name,
        )

    logger.info(
        "Successfully upserted %s tasks for %s", len(tasks_to_upsert), config.name
    )
    return len(tasks_to_upsert)


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
