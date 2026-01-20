from django.core.cache import cache

from ticket_dashboard.services.eramba import ErambaService
from ticket_dashboard.services.espocrm import EspoService
from ticket_dashboard.services.gitlab import GitLabService
from ticket_dashboard.services.openproject import OpenProjectService
from ticket_dashboard.services.zammad import ZammadService
from ticket_dashboard.users.models import ServiceConfiguration

MAX_HEALTHY_LATENCY_MS = 1000


def system_status(request):  # noqa: C901
    """
    Adds 'services_status' and 'global_system_status' to context.
    - List of services is fetched LIVE from DB (Instant Admin Toggle).
    - Health of each service is CACHED individually (Performance).
    """
    if not request.user.is_authenticated:
        return {}

    # 1. Check for Forced Refresh
    force_refresh = request.GET.get("refresh") == "1"

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

        # If refresh is requested OR no cache exists, fetch fresh
        if force_refresh or health is None:
            service_instance = service_class(config)
            health = service_instance.check_health()
            # Cache this specific result for 5 minutes
            cache.set(cache_key, health, timeout=300)

        results.append(health)

        if health["status"] == "online":
            latencies.append(health["latency"])
        else:
            pass

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
    }
