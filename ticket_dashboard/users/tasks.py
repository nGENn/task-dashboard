import logging

from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime

from ticket_dashboard.services.eramba import ErambaService
from ticket_dashboard.services.espocrm import EspoService
from ticket_dashboard.services.gitlab import GitLabService
from ticket_dashboard.services.openproject import OpenProjectService
from ticket_dashboard.services.zammad import ZammadService

from .models import ExternalGroup
from .models import ServiceConfiguration
from .models import Ticket

logger = logging.getLogger(__name__)

SERVICE_CLASSES = {
    "zammad": ZammadService,
    "gitlab": GitLabService,
    "espocrm": EspoService,
    "openproject": OpenProjectService,
    "eramba": ErambaService,
}


def fetch_service_tickets(config_id: int):
    """
    Fetches tickets for a specific service configuration and performs batch upsert.
    """
    try:
        config = ServiceConfiguration.objects.get(pk=config_id, is_active=True)
    except ServiceConfiguration.DoesNotExist:
        logger.error("ServiceConfiguration with id %s not found or inactive.", config_id)
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
        tickets_data = service_instance.get_tickets(force_refresh=True)
    except Exception:
        logger.exception("Error fetching tasks for service %s", config.name)
        return 0

    tickets_to_upsert = {}  # Use dict to handle unique tickets per service fetch
    groups_to_upsert = {}  # Use dict to handle unique groups per service fetch

    for ticket_dict in tickets_data:
        # Date Parsing
        def parse_dt(dt_str):
            if not dt_str:
                return None
            dt = parse_datetime(dt_str)
            if dt and django_timezone.is_naive(dt):
                return django_timezone.make_aware(dt)
            return dt

        ticket_id = ticket_dict["id"]
        tickets_to_upsert[ticket_id] = Ticket(
            service=config,
            external_id=ticket_id,
            title=ticket_dict.get("title", ""),
            status=ticket_dict.get("status", ""),
            priority=ticket_dict.get("priority", ""),
            customer=ticket_dict.get("customer", ""),
            group=ticket_dict.get("group", ""),
            owner=ticket_dict.get("owner", ""),
            owner_email=ticket_dict.get("owner_email", "") or "",
            url=ticket_dict.get("url", ""),
            created_at=parse_dt(ticket_dict.get("created_at")),
            updated_at=parse_dt(ticket_dict.get("updated_at")),
            due_date=parse_dt(ticket_dict.get("due_date")),
        )

        group_name = ticket_dict.get("group")
        if group_name:
            groups_to_upsert[(config.name, group_name)] = ExternalGroup(
                origin=config.name,
                name=group_name,
                extra_data=ticket_dict.get("extra_info", {}),
            )

    # Perform Batch Upserts
    if groups_to_upsert:
        ExternalGroup.objects.bulk_create(
            groups_to_upsert.values(),
            update_conflicts=True,
            unique_fields=["origin", "name"],
            update_fields=["extra_data", "last_seen"],
        )

    if tickets_to_upsert:
        Ticket.objects.bulk_create(
            tickets_to_upsert.values(),
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

        # PRUNING: Remove tickets that are no longer in the service
        deleted_count, _ = Ticket.objects.filter(service=config).exclude(
            external_id__in=tickets_to_upsert.keys(),
        ).delete()
        if deleted_count:
            logger.info("Pruned %s stale tasks for %s", deleted_count, config.name)

    logger.info("Successfully upserted %s tasks for %s", len(tickets_to_upsert), config.name)
    return len(tickets_to_upsert)


def fetch_all_tickets_task():
    """
    Main task to trigger ticket fetching for all active services.
    Dispatches individual service fetches in parallel.
    """
    from django_q.tasks import async_task

    active_configs = ServiceConfiguration.objects.filter(is_active=True)
    for config in active_configs:
        logger.info("Dispatching parallel fetch for service: %s", config.name)
        async_task("ticket_dashboard.users.tasks.fetch_service_tickets", config.id)

    return active_configs.count()
