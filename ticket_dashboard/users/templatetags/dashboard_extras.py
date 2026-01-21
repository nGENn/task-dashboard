import datetime

from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Replaces GET parameters in the URL while keeping existing ones.
    Usage: {% url_replace sort='title' direction='asc' %}
    """
    query = context["request"].GET.copy()
    for k, v in kwargs.items():
        query[k] = v
    return query.urlencode()


@register.filter
def toggle_direction(value):
    """
    Returns 'desc' if value is 'asc', otherwise returns 'asc'.
    Usage: {{ current_dir|toggle_direction }}
    """
    return "desc" if value == "asc" else "asc"


@register.simple_tag
def is_active_view(request_get, view_params):
    """
    Compares request.GET (QueryDict) with view_params (dict).
    Returns True if they match (ignoring order).
    """
    if not isinstance(view_params, dict):
        return False

    # Normalize request_get to a dict of sorted lists
    # request_get is usually request.GET which is a QueryDict
    req_dict = {}
    for key in request_get.keys():
        req_dict[key] = sorted(request_get.getlist(key))

    # Normalize view_params to a dict of sorted lists
    vp_dict = {}
    for key, value in view_params.items():
        if isinstance(value, list):
            vp_dict[key] = sorted([str(v) for v in value])
        else:
            vp_dict[key] = [str(value)]

    # Clean up empty values in req_dict if they are not in vp_dict
    # This handles the case where '?' or empty params are present
    req_dict = {k: v for k, v in req_dict.items() if v != [""] or k in vp_dict}
    vp_dict = {k: v for k, v in vp_dict.items() if v != [""]}

    return req_dict == vp_dict


@register.filter
def dynamic_date(value):
    """
    Formats a date dynamically:
    - If today: relative duration (e.g., '2h 15m ago' or '3h 10m left')
    - If not today: dd/mm/yyyy
    """
    if not value:
        return "-"

    # Handle both string and datetime objects
    has_time = False
    if isinstance(value, str):
        try:
            # Try to parse yyyy-mm-dd (date only)
            dt = datetime.datetime.strptime(value, "%Y-%m-%d")
            dt = dt.date()
            has_time = False
        except ValueError:
            try:
                # Try to parse ISO format (usually with time)
                dt = datetime.datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                )
                has_time = True
            except ValueError:
                return value
    else:
        dt = value
        has_time = isinstance(dt, datetime.datetime)

    now = timezone.now()
    if isinstance(dt, datetime.datetime):
        dt_for_comparison = dt.date()
        dt_full = dt
    else:
        dt_for_comparison = dt
        dt_full = datetime.datetime.combine(
            dt,
            datetime.time.min,
            tzinfo=timezone.get_current_timezone(),
        )

    today = now.date()

    if dt_for_comparison == today:
        if not has_time:
            return "Today"

        # If dt_full has no timezone, make it aware
        if timezone.is_naive(dt_full):
            dt_full = timezone.make_aware(dt_full)

        diff = dt_full - now
        seconds = diff.total_seconds()
        is_future = seconds > 0
        seconds = abs(seconds)

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        suffix = " left" if is_future else " ago"

        if hours > 0:
            return f"{hours}h {minutes}m{suffix}"
        return f"{minutes}m{suffix}"

    # Default format for other days
    return dt_for_comparison.strftime("%d/%m/%Y")

    # Default format for other days
    return dt_for_comparison.strftime("%d/%m/%Y")
