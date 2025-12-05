from django import template

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
