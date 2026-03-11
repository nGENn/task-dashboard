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

MAX_HEALTHY_LATENCY_MS = 1000


def system_status(request):  # noqa: C901
    """
    Adds 'services_status', 'global_system_status', and 'next_refresh_seconds' to context.
    - List of services is fetched LIVE from DB (Instant Admin Toggle).
    - Health of each service is CACHED individually (Performance).
    """
    if not request.user.is_authenticated:
        return {}

    if not (
        request.user.is_staff
        or request.user.is_superuser
        or request.user.has_perm("users.view_system_health")
        or request.user.has_perm("users.view_admin_button")
    ):
        return {}

    # 1. Calculate time until next refresh from Django Q schedule
    next_refresh_seconds = None
    refresh_interval = 5  # Default fallback minutes
    try:
        schedule = Schedule.objects.filter(name="Fetch All Tasks").first()
        if schedule:
            refresh_interval = schedule.minutes or 5
            if schedule.next_run:
                diff = (schedule.next_run - timezone.now()).total_seconds()
                # Only show countdown if the next run is actually in the future
                if diff > 0:
                    next_refresh_seconds = math.ceil(diff)
    except Exception:
        pass

    # 2. Define Available Services Map (Service Type -> Class)
    service_map = {
        "eramba": ErambaService,
        "espocrm": EspoService,
        "gitlab": GitLabService,
        "openproject": OpenProjectService,
        "zammad": ZammadService,
    }

    # 3. Get ONLY Active Configs from DB
    # We query this every time so the Admin Toggle is instant.
    # It's a tiny table, so the performance cost is negligible.
    active_configs = ServiceConfiguration.objects.filter(
        is_active=True,
        service_type__in=service_map.keys(),
    ).order_by("name")

    results = []
    latencies = []

    for config in active_configs:
        # Get the class
        service_class = service_map.get(config.service_type)
        if service_class is None:
            continue

        # 4. Per-Service Caching
        # We look for a cached result for THIS specific service instance
        cache_key = f"health_check_result_{config.pk}"
        health = cache.get(cache_key)

        # If no cache exists, fetch fresh
        if health is None:
            service_instance = service_class(config)
            health = service_instance.check_health()
            # Cache this specific result for 5 minutes
            cache.set(cache_key, health, timeout=300)

        if health["status"] == "online":
            latencies.append(health["latency"])
            if health["latency"] > MAX_HEALTHY_LATENCY_MS:
                health["status"] = "degraded"

        results.append(health)

    # 5. Calculate Global State
    max_latency = max(latencies) if latencies else 0

    # Extract list of statuses for easier checking
    status_list = [r["status"] for r in results]

    if not results:
        global_state = "No Services"
        global_color = "neutral"
    elif "offline" in status_list:
        global_state = "Offline"
        global_color = "error"
    elif "auth_error" in status_list:
        global_state = "Auth Error"
        global_color = "warning"
    elif "auth_missing" in status_list:
        global_state = "Setup Needed"
        global_color = "warning"
    elif max_latency > MAX_HEALTHY_LATENCY_MS:
        global_state = "Degraded"
        global_color = "warning"
    else:
        global_state = "Healthy"
        global_color = "success"

    return {
        "services_status": results,
        "global_system_status": {
            "state": global_state,
            "color": global_color,
            "max_latency": max_latency,
        },
        "next_refresh_seconds": next_refresh_seconds,
        "refresh_interval": refresh_interval,
    }
