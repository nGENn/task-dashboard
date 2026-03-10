from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from task_dashboard.services.eramba import ErambaService


@pytest.fixture
def eramba_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://eramba.example.com"
    config.api_username = "user"
    config.api_password = "password"
    config.name = "Eramba Test"
    return config

@pytest.fixture
def eramba_service(eramba_config):
    return ErambaService(eramba_config)

@pytest.mark.anyio
async def test_get_tasks_async_fetches_all_modules(eramba_service):
    # Mock httpx.AsyncClient.get
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 1, "title": "Test Task"}]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # 9 modules are defined in the service
        assert mock_get.call_count == 9
        # Each module returns 1 task, so total 9
        assert len(tasks) == 9
        assert tasks[0]["title"] == "Test Task"

@pytest.mark.anyio
async def test_pagination_works(eramba_service):
    # We want to test that it keeps fetching if len(items) == limit

    # Setup mock responses for different pages
    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = [{"id": i, "title": f"Task {i}"} for i in range(100)]

    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = [{"id": i, "title": f"Task {i}"} for i in range(100, 150)]

    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.json.return_value = []

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        # Side effect to handle multiple calls
        # Note: get_tasks_async calls 9 modules.
        # For simplicity, let's just mock them all.

        def side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            page = params.get("page", 1)
            if "api/projects" in url:
                if page == 1:
                    return page1_resp
                if page == 2:
                    return page2_resp
            return empty_resp

        mock_get.side_effect = side_effect

        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # Projects should have contributed 150 tasks
        assert len(tasks) == 150

@pytest.mark.anyio
async def test_owner_mapping_variations(eramba_service):
    # Test that it correctly picks up owners from different fields
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": 1, "title": "T1", "owners": [{"user": {"email": "o1@e.com"}}]},
        {"id": 2, "title": "T2", "reviewers": [{"user": {"email": "r1@e.com"}}]},
        {"id": 3, "title": "T3", "task_owners": [{"user": {"email": "t1@e.com"}}]},
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        # To avoid noise from 9 modules, let's just check one result set
        # Actually it will call 9 modules, each returning 3 tasks = 27 tasks
        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        emails = [t["owner"] for t in tasks]
        assert "o1@e.com" in emails
        assert "r1@e.com" in emails
        assert "t1@e.com" in emails

@pytest.mark.anyio
async def test_view_url_correctness(eramba_service):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 42, "title": "URL Test"}]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        # We need to check a specific module's URL
        # Let's say the first module is security-incidents
        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # Found tasks[0] which corresponds to the first module in modules_to_fetch
        # web_path for security-incidents is "security-incidents", model_class is "SecurityIncidents"
        assert tasks[0]["url"] == "https://eramba.example.com/security-incidents/view/SecurityIncidents/42"
        # Ensure model class (SecurityIncidents) IS in the URL
        assert "SecurityIncidents" in tasks[0]["url"]
