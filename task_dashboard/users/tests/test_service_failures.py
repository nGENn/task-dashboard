from unittest.mock import patch

import httpx
import pytest

from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.tasks import fetch_service_tasks


@pytest.fixture
def zammad_config(db):
    return ServiceConfiguration.objects.create(
        name="Zammad",
        service_type="zammad",
        api_url="https://zammad.example.com",
        api_token="test-token",  # noqa: S106
        is_active=True,
    )


@pytest.mark.django_db
def test_fetch_service_tasks_timeout(zammad_config, caplog):
    # Mock timeout in the sync wrapper
    with patch("httpx.AsyncClient.get", side_effect=httpx.TimeoutException("Timeout")):
        with caplog.at_level("ERROR"):
            count = fetch_service_tasks(zammad_config.id)

        assert count == 0
        assert "Error fetching tasks for service" in caplog.text


@pytest.mark.django_db
def test_fetch_service_tasks_rate_limit(zammad_config, caplog):
    # Mock 429 Too Many Requests
    request = httpx.Request("GET", "https://zammad.example.com")
    mock_resp = httpx.Response(429, request=request)
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        with caplog.at_level("ERROR"):
            count = fetch_service_tasks(zammad_config.id)

        assert count == 0
        assert "Error fetching tasks for service" in caplog.text


@pytest.mark.django_db
def test_fetch_service_tasks_server_error(zammad_config, caplog):
    # Mock 500 Server Error
    request = httpx.Request("GET", "https://zammad.example.com")
    mock_resp = httpx.Response(500, request=request)
    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        with caplog.at_level("ERROR"):
            count = fetch_service_tasks(zammad_config.id)

        assert count == 0
        assert "Error fetching tasks for service" in caplog.text
