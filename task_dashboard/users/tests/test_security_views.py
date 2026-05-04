from unittest.mock import patch

import pytest
from django.urls import reverse

from task_dashboard.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_force_refresh_view_requires_staff(client):
    """Verify that force_refresh_view is restricted to staff members."""
    user = UserFactory.create(is_staff=False)
    client.force_login(user)

    url = reverse("users:force-refresh")
    response = client.get(url)

    # Django's user_passes_test redirects to login page by default for non-staff
    assert response.status_code == 302  # noqa: PLR2004
    assert "login" in response.url


def test_force_refresh_view_allowed_for_staff(client):
    """Verify that staff members can access force_refresh_view."""
    # Mock the background task to avoid actually running it
    with patch("task_dashboard.users.views.fetch_all_tasks_task") as mock_fetch:
        user = UserFactory.create(is_staff=True)
        client.force_login(user)

        url = reverse("users:force-refresh")
        response = client.get(url)

        # Should redirect back to referer or home
        assert response.status_code == 302  # noqa: PLR2004
        assert mock_fetch.called
