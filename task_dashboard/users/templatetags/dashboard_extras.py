import datetime

from django import template
from django.utils import timezone
from django.utils.translation import gettext as _

from task_dashboard.users.models import compare_query_params

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
    return compare_query_params(request_get, view_params)


@register.filter(name="translate")
def translate_filter(value):
    """
    Simple filter to translate a string value dynamically.
    Normalizes to title case for common states/priorities to match PO file.
    """
    if not value:
        return value
    s_value = str(value)
    # Try direct translation
    translated = _(s_value)
    if translated != s_value:
        return translated

    # Try Title Case (matches our DUMMY_STRINGS)
    title_value = s_value.title()
    return _(title_value)


@register.filter(name="translate_list")
def translate_list_filter(value_list):
    """
    Translates a list of strings and joins them.
    """
    if not value_list:
        return ""
    if isinstance(value_list, str):
        return translate_filter(value_list)
    return ", ".join([str(translate_filter(v)) for v in value_list])


def _parse_dynamic_date(value):
    """Helper to parse input into (datetime, has_time) or (None, value)."""
    if not isinstance(value, str):
        return value, isinstance(value, datetime.datetime)

    try:
        # Try to parse yyyy-mm-dd (date only)
        dt = (
            datetime.datetime.strptime(value, "%Y-%m-%d")
            .replace(tzinfo=datetime.UTC)
            .date()
        )
    except ValueError:
        pass
    else:
        return dt, False

    try:
        # Try to parse ISO format (usually with time)
        dt = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None, value
    else:
        return dt, True


def _format_relative_time(dt_full, now):
    """Helper to format relative time strings."""
    if timezone.is_naive(dt_full):
        dt_full = timezone.make_aware(dt_full)

    diff = dt_full - now
    seconds = abs(diff.total_seconds())
    hours, minutes = int(seconds // 3600), int((seconds % 3600) // 60)

    if diff.total_seconds() > 0:  # Future
        if hours > 0:
            return _("%(hours)dh %(minutes)dm left") % {
                "hours": hours,
                "minutes": minutes,
            }
        return _("%(minutes)dm left") % {"minutes": minutes}

    # Past
    if hours > 0:
        return _("%(hours)dh %(minutes)dm ago") % {"hours": hours, "minutes": minutes}
    return _("%(minutes)dm ago") % {"minutes": minutes}


@register.filter
def dynamic_date(value):
    """
    Formats a date dynamically:
    - If today: relative duration (e.g., '2h 15m ago' or '3h 10m left')
    - If not today: dd/mm/yyyy
    """
    if not value:
        return "-"

    dt, has_time = _parse_dynamic_date(value)
    if dt is None:
        return has_time  # Return the original string value on parse error

    now = timezone.now()
    if isinstance(dt, datetime.datetime):
        dt_for_comparison, dt_full = dt.date(), dt
    else:
        dt_for_comparison = dt
        dt_full = datetime.datetime.combine(
            dt, datetime.time.min, tzinfo=timezone.get_current_timezone()
        )

    if dt_for_comparison != now.date():
        return dt_for_comparison.strftime("%d/%m/%Y")

    if not has_time:
        return _("Today")

    return _format_relative_time(dt_full, now)


# Dummy strings for makemessages to pick up common normalized values
_DUMMY_STRINGS = [
    _("Critical"),
    _("High"),
    _("Medium"),
    _("Low"),
    _("Open"),
    _("Pending"),
    _("Resolved"),
    _("Closed"),
    _("New"),
]
