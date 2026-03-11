import datetime

from django import template
from django.utils import timezone

register = template.Library()


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """
    Replaces GET parameters in the URL while keeping existing ones.
    If a value is None, the parameter is removed.
    Usage: {% url_replace sort='title' direction='asc' %}
    """
    query = context["request"].GET.copy()
    for k, v in kwargs.items():
        if v is None:
            if k in query:
                del query[k]
        else:
            query[k] = v
    return query.urlencode()


@register.simple_tag(takes_context=True)
def sort_url(context, field):
    """
    Generates a URL for three-state sorting: None -> ASC -> DESC -> None.
    """
    request = context["request"]
    current_sort = request.GET.get("sort")
    current_dir = request.GET.get("direction")

    if current_sort == field:
        if current_dir == "asc":
            next_sort = field
            next_dir = "desc"
        elif current_dir == "desc":
            next_sort = None
            next_dir = None
        else:
            next_sort = field
            next_dir = "asc"
    else:
        next_sort = field
        next_dir = "asc"

    return url_replace(context, sort=next_sort, direction=next_dir)


@register.filter
def toggle_direction(value):
    """
    Returns 'desc' if value is 'asc', otherwise returns 'asc'.
    Used for simple toggles.
    """
    return "desc" if value == "asc" else "asc"


@register.simple_tag
def is_active_view(request_get, view_params):
    """
    Compares request.GET (QueryDict) with view_params (dict).
    Returns True if they match (ignoring order and specific params like page/sort).
    """
    if not isinstance(view_params, dict):
        return False

    # Parameters to ignore when comparing active view
    ignore_params = {"page", "sort", "direction"}

    # Normalize request_get to a dict of sorted lists
    req_dict = {}
    for key in request_get:
        if key not in ignore_params:
            req_dict[key] = sorted(request_get.getlist(key))

    # Normalize view_params to a dict of sorted lists
    vp_dict = {}
    for key, value in view_params.items():
        if key not in ignore_params:
            if isinstance(value, list):
                vp_dict[key] = sorted([str(v) for v in value])
            else:
                vp_dict[key] = [str(value)]

    # Clean up empty values
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
            dt = (
                datetime.datetime.strptime(value, "%Y-%m-%d")
                .replace(
                    tzinfo=datetime.UTC,
                )
                .date()
            )
            has_time = False
        except ValueError:
            try:
                # Try to parse ISO format (usually with time)
                dt = datetime.datetime.fromisoformat(
                    value.replace("Z", "+00:00"),
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

    if dt_for_comparison != now.date():
        return dt_for_comparison.strftime("%d/%m/%Y")

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
