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
