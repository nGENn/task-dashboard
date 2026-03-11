import pytest
from django.urls import reverse
from task_dashboard.users.models import SavedView

pytestmark = pytest.mark.django_db

def test_save_view_with_sort(client, user):
    client.force_login(user)
    url = reverse("users:save_view")
    data = {
        "name": "Sorted View",
        "query_params": {
            "sort": "title",
            "direction": "asc",
            "state": ["open"]
        }
    }
    response = client.post(url, data=data, content_type="application/json")
    assert response.status_code == 200
    
    saved_view = SavedView.objects.get(user=user, name="Sorted View")
    assert saved_view.query_params["sort"] == "title"
    assert saved_view.query_params["direction"] == "asc"
    
    # Verify the query string generation
    query_string = saved_view.get_query_string()
    assert "sort=title" in query_string
    assert "direction=asc" in query_string
    assert "state=open" in query_string

def test_is_active_view_ignores_sort_and_page(user):
    from task_dashboard.users.templatetags.dashboard_extras import is_active_view
    from django.http import QueryDict
    
    view_params = {"view": "all", "state": ["open"]}
    
    # Exact match
    request_get = QueryDict("view=all&state=open")
    assert is_active_view(request_get, view_params) is True
    
    # Match with different sort
    request_get = QueryDict("view=all&state=open&sort=title&direction=desc")
    assert is_active_view(request_get, view_params) is True
    
    # Match with page
    request_get = QueryDict("view=all&state=open&page=2")
    assert is_active_view(request_get, view_params) is True
    
    # Different filter should not match
    request_get = QueryDict("view=all&state=resolved")
    assert is_active_view(request_get, view_params) is False

def test_sort_url_three_state_cycle(user, rf):
    from task_dashboard.users.templatetags.dashboard_extras import sort_url
    
    # State 1: No sort -> ASC
    request = rf.get("/")
    context = {"request": request}
    url = sort_url(context, "title")
    assert "sort=title" in url
    assert "direction=asc" in url
    
    # State 2: ASC -> DESC
    request = rf.get("/?sort=title&direction=asc")
    context = {"request": request}
    url = sort_url(context, "title")
    assert "sort=title" in url
    assert "direction=desc" in url
    
    # State 3: DESC -> None
    request = rf.get("/?sort=title&direction=desc")
    context = {"request": request}
    url = sort_url(context, "title")
    assert "sort" not in url
    assert "direction" not in url
    
    # State 4: Different field -> ASC
    request = rf.get("/?sort=status&direction=asc")
    context = {"request": request}
    url = sort_url(context, "title")
    assert "sort=title" in url
    assert "direction=asc" in url
