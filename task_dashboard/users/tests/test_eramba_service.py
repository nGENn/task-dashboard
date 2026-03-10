from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.utils import timezone

from task_dashboard.services.eramba import ErambaService

# Module count constant to avoid magic numbers
MODULE_COUNT = 9


@pytest.fixture
def eramba_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://eramba.example.com"
    config.api_username = "user"
    config.api_password = "password"  # noqa: S105
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

        # MODULE_COUNT modules are defined in the service
        assert mock_get.call_count == MODULE_COUNT
        # Each module returns 1 task
        assert len(tasks) == MODULE_COUNT
        assert tasks[0]["title"] == "Test Task"


@pytest.mark.anyio
async def test_pagination_works(eramba_service):
    # We want to test that it keeps fetching if len(items) == limit
    limit = 100
    total_expected = 150

    # Setup mock responses for different pages
    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = [
        {"id": i, "title": f"Task {i}"} for i in range(limit)
    ]

    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = [
        {"id": i, "title": f"Task {i}"} for i in range(limit, total_expected)
    ]

    empty_resp = MagicMock()
    empty_resp.status_code = 200
    empty_resp.json.return_value = []

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:

        def side_effect(url, **kwargs):
            params = kwargs.get("params", {})
            page = params.get("page", 1)
            if "api/projects" in url:
                if page == 1:
                    return page1_resp
                if page == 2:  # noqa: PLR2004
                    return page2_resp
            return empty_resp

        mock_get.side_effect = side_effect

        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # Projects should have contributed total_expected tasks
        assert len(tasks) == total_expected


@pytest.mark.anyio
async def test_owner_mapping_variations(eramba_service):
    # Test that it correctly picks up owners from different fields
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {"id": 1, "title": "T1", "owners": [{"user": {"email": "o1@e.com"}}]},
        {"id": 2, "title": "T2", "reviewers": [{"user": {"email": "r1@e.com"}}]},
        {"id": 3, "title": "T3", "task_owners": [{"user": {"email": "t1@e.com"}}]},
        {
            "id": 4,
            "title": "T4",
            "owners": [
                {
                    "id": 6289,
                    "model": "AssetReviews",
                    "group": {"id": 30, "name": "IT Head"},
                }
            ],
        },
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        # To avoid noise from MODULE_COUNT modules, let's just check one result set
        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        owners = [t["owner"] for t in tasks]
        assert "o1@e.com" in owners
        assert "r1@e.com" in owners
        assert "t1@e.com" in owners
        assert "IT Head" in owners


@pytest.mark.anyio
async def test_view_url_correctness(eramba_service):
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"id": 42, "title": "URL Test"}]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # web_path for security-incidents is "security-incidents",
        # model_class is "SecurityIncidents"
        expected_url = (
            "https://eramba.example.com/security-incidents/view/SecurityIncidents/42"
        )
        assert tasks[0]["url"] == expected_url
        assert "SecurityIncidents" in tasks[0]["url"]


@pytest.mark.anyio
async def test_future_task_filtering(eramba_service):
    now = timezone.now()
    within_window = (now + timezone.timedelta(days=15)).strftime("%Y-%m-%d")
    outside_window = (now + timezone.timedelta(days=45)).strftime("%Y-%m-%d")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [
        {
            "id": 1,
            "title": "Open - Within",
            "status": "open",
            "planned_date": within_window,
        },
        {
            "id": 2,
            "title": "Open - Outside",
            "status": "open",
            "planned_date": outside_window,
        },
        {
            "id": 3,
            "title": "Closed - Outside",
            "status": "closed",
            "closure_date": outside_window,
        },
        {
            "id": 4,
            "title": "Pending - Outside",
            "status": "pending",
            "project_status_id": 1,
            "deadline": outside_window,
        },
    ]

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        tasks = await eramba_service.get_tasks_async(force_refresh=True)

        # Expected:
        # 1. Included (open, within window)
        # 2. Excluded (open, outside window)
        # 3. Included (closed, window ignored)
        # 4. Included (pending, window ignored)
        # Total per module: 3 tasks. Total: 3 * MODULE_COUNT
        total_expected = 3 * MODULE_COUNT
        assert len(tasks) == total_expected
        titles = [t["title"] for t in tasks]
        assert "Open - Within" in titles
        assert "Open - Outside" not in titles
        assert "Closed - Outside" in titles
        assert "Pending - Outside" in titles
