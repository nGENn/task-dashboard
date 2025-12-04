from django.core.cache import cache

from ticket_dashboard.services.gitlab import GitLabService
from ticket_dashboard.services.zammad import ZammadService
from ticket_dashboard.users.models import ServiceConfiguration


def system_status(request):
    """
    Adds 'services_status' and 'global_system_status' to context.
    - List of services is fetched LIVE from DB (Instant Admin Toggle).
    - Health of each service is CACHED individually (Performance).
    """
    if not request.user.is_authenticated:
        return {}

    # 1. Check for Forced Refresh
    force_refresh = request.GET.get("refresh") == "1"

    # 2. Define Available Services Map (Name -> Class)
    service_map = {
        "Zammad": ZammadService,
        "GitLab": GitLabService,
    }

    # 3. Get ONLY Active Configs from DB
    # We query this every time so the Admin Toggle is instant.
    # It's a tiny table, so the performance cost is negligible.
    active_configs = ServiceConfiguration.objects.filter(
        is_active=True, name__in=service_map.keys()
    )

    results = []
    latencies = []
    any_offline = False

    for config in active_configs:
        # Get the class
        ServiceClass = service_map.get(config.name)

        # 4. Per-Service Caching
        # We look for a cached result for THIS specific service
        cache_key = f"health_check_result_{config.name}"
        health = cache.get(cache_key)

        # If refresh is requested OR no cache exists, fetch fresh
        if force_refresh or health is None:
            service_instance = ServiceClass()
            health = service_instance.check_health()
            # Cache this specific result for 5 minutes
            cache.set(cache_key, health, timeout=300)

        results.append(health)

        if health["status"] == "online":
            latencies.append(health["latency"])
        else:
            any_offline = True

    # 5. Calculate Global State
    max_latency = max(latencies) if latencies else 0

    if not results:
        global_state = "No Services"
        global_color = "neutral"
    elif any_offline:
        global_state = "Offline"
        global_color = "error"
    elif max_latency > 1000:
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
