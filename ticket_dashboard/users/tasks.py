import logging

from django.utils.dateparse import parse_datetime

from ticket_dashboard.services.eramba import ErambaService
from ticket_dashboard.services.espocrm import EspoService
from ticket_dashboard.services.gitlab import GitLabService
from ticket_dashboard.services.openproject import OpenProjectService
from ticket_dashboard.services.zammad import ZammadService

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


def fetch_all_tickets_task():
    """
    Background task to fetch tasks from all active services and upsert them.
    """
    active_configs = ServiceConfiguration.objects.filter(is_active=True)
    total_upserted = 0

    for config in active_configs:
        try:
            service_class = SERVICE_CLASSES.get(config.service_type)
            if not service_class:
                logger.error(
                    "Unknown service type '%s' for configuration '%s'",
                    config.service_type,
                    config.name,
                )
                continue

            logger.info(
                "Fetching tasks for service: %s (%s)",
                config.name,
                config.service_type,
            )
            service_instance = service_class(config)
            tickets_data = service_instance.get_tickets(force_refresh=True)

            service_upsert_count = 0
            for ticket_dict in tickets_data:
                # Date Parsing
                created_at = (
                    parse_datetime(ticket_dict.get("created_at"))
                    if ticket_dict.get("created_at")
                    else None
                )
                updated_at = (
                    parse_datetime(ticket_dict.get("updated_at"))
                    if ticket_dict.get("updated_at")
                    else None
                )
                due_date = (
                    parse_datetime(ticket_dict.get("due_date"))
                    if ticket_dict.get("due_date")
                    else None
                )

                Ticket.objects.update_or_create(
                    service=config,
                    external_id=ticket_dict["id"],
                    defaults={
                        "title": ticket_dict.get("title", ""),
                        "status": ticket_dict.get("status", ""),
                        "priority": ticket_dict.get("priority", ""),
                        "customer": ticket_dict.get("customer", ""),
                        "group": ticket_dict.get("group", ""),
                        "owner": ticket_dict.get("owner", ""),
                        "owner_email": ticket_dict.get("owner_email", "") or "",
                        "url": ticket_dict.get("url", ""),
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "due_date": due_date,
                    },
                )
                service_upsert_count += 1

            logger.info(
                "Successfully upserted %s tasks for %s",
                service_upsert_count,
                config.name,
            )
            total_upserted += service_upsert_count

        except Exception:
            logger.exception("Error fetching tasks for service %s", config.name)

    logger.info("Total tasks upserted across all services: %s", total_upserted)
    return total_upserted
