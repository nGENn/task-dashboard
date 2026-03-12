import logging
import math

from django.core.cache import cache
from django.utils import timezone
from django_q.models import Schedule

from task_dashboard.services.eramba import ErambaService
from task_dashboard.services.espocrm import EspoService
from task_dashboard.services.gitlab import GitLabService
from task_dashboard.services.openproject import OpenProjectService
from task_dashboard.services.zammad import ZammadService
from task_dashboard.users.models import ServiceConfiguration

logger = logging.getLogger(__name__)

MAX_HEALTHY_LATENCY_MS = 2000


def system_status(request):
    """
    Adds system status information to the template context.
    - services_status: List of health results for active services.
    - global_system_status: Overall system health state.
    - next_refresh_seconds: Time until next task fetch.
    """
    if not request.user.is_authenticated or not _has_status_permission(request.user):
        return {}

    # Map is defined inside to allow for easier patching in tests
    service_map = {
        "eramba": ErambaService,
        "espocrm": EspoService,
        "gitlab": GitLabService,
        "openproject": OpenProjectService,
        "zammad": ZammadService,
    }

    next_refresh_seconds, refresh_interval = _get_refresh_info()
    results = _get_services_health(service_map)
    global_status = _calculate_global_status(results)

    return {
        "services_status": results,
        "global_system_status": global_status,
        "next_refresh_seconds": next_refresh_seconds,
        "refresh_interval": refresh_interval,
    }


def _has_status_permission(user):
    """Checks if the user has permission to view system status."""
    return (
        user.is_staff
        or user.is_superuser
        or user.has_perm("users.view_system_health")
        or user.has_perm("users.view_admin_button")
    )


def _get_refresh_info():
    """Calculates time until next refresh from Django Q schedule."""
    next_refresh_seconds = None
    refresh_interval = 5  # Default fallback minutes
    try:
        schedule = Schedule.objects.filter(name="Fetch All Tasks").first()
        if schedule:
            refresh_interval = schedule.minutes or 5
            if schedule.next_run:
                diff = (schedule.next_run - timezone.now()).total_seconds()
                if diff > 0:
                    next_refresh_seconds = math.ceil(diff)
    except Exception:  # noqa: BLE001
        # Silently fail for schedule calculation issues to avoid crashing UI.
        # We use a broad catch here because Schedule might not exist or have weird data.
        logger.debug("Failed to calculate next refresh time", exc_info=True)

    return next_refresh_seconds, refresh_interval


def _get_services_health(service_map):
    """Fetches and caches health status for all active services."""
    active_configs = ServiceConfiguration.objects.filter(
        is_active=True,
        service_type__in=service_map.keys(),
    ).order_by("name")

    results = []
    for config in active_configs:
        service_class = service_map.get(config.service_type)
        if not service_class:
            continue

        cache_key = f"health_check_result_{config.pk}"
        health = cache.get(cache_key)

        if health is None:
            try:
                service_instance = service_class(config)
                health = service_instance.check_health()
                cache.set(cache_key, health, timeout=300)
            except Exception:
                logger.exception("Health check failed for %s", config.name)
                health = {
                    "status": "offline",
                    "name": config.name,
                    "latency": 0,
                    "error": "Internal Error during health check",
                }

        if health["status"] == "online" and health["latency"] > MAX_HEALTHY_LATENCY_MS:
            health["status"] = "degraded"

        results.append(health)
    return results


def _calculate_global_status(results):
    """Determines the global system health state and color."""
    latencies = [r["latency"] for r in results if r["status"] == "online"]
    max_latency = max(latencies) if latencies else 0
    status_list = [r["status"] for r in results]

    if not results:
        state, color = "No Services", "neutral"
    elif "offline" in status_list:
        state, color = "Offline", "error"
    elif "auth_error" in status_list:
        state, color = "Auth Error", "warning"
    elif "auth_missing" in status_list:
        state, color = "Setup Needed", "warning"
    elif (
        any(r["status"] == "degraded" for r in results)
        or max_latency > MAX_HEALTHY_LATENCY_MS
    ):
        state, color = "Degraded", "warning"
    else:
        state, color = "Healthy", "success"

    return {
        "state": state,
        "color": color,
        "max_latency": max_latency,
    }
