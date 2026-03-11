from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from task_dashboard.services.espocrm import EspoService


@pytest.fixture
def espo_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://espo.example.com"
    config.api_token = "test-token"  # noqa: S105
    config.name = "Espo Test"
    return config


@pytest.fixture
def espo_service(espo_config):
    return EspoService(espo_config)


@pytest.mark.anyio
async def test_get_tasks_async_uses_id_when_number_is_missing(espo_service):
    # Mock data for Case (has number) and Task (no number)
    case_data = {
        "list": [
            {"id": "case-uuid-1", "number": 101, "name": "Case 1", "status": "New"},
        ]
    }
    task_data = {
        "list": [
            {"id": "task-uuid-1", "name": "Task 1", "status": "Not Started"},
            {"id": "task-uuid-2", "name": "Task 2", "status": "In Progress"},
        ]
    }
    user_data = {"list": []}

    def side_effect(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        if "api/v1/User" in url:
            mock_resp.json = MagicMock(return_value=user_data)
        elif "api/v1/Case" in url:
            mock_resp.json = MagicMock(return_value=case_data)
        elif "api/v1/Task" in url:
            mock_resp.json = MagicMock(return_value=task_data)
        else:
            mock_resp.json = MagicMock(return_value={"list": []})
        return mock_resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = side_effect

        tasks = await espo_service.get_tasks_async(force_refresh=True)

        # We expect 3 tasks total (1 case + 2 tasks)
        assert len(tasks) == 3  # noqa: PLR2004

        ids = [t["id"] for t in tasks]
        # Case should use its number
        assert "ESPO-C-101" in ids
        # Tasks should use their UUIDs because they don't have numbers
        assert "ESPO-T-task-uuid-1" in ids
        assert "ESPO-T-task-uuid-2" in ids


@pytest.mark.anyio
async def test_get_tasks_async_parallel_fetching(espo_service):
    # Ensure gather is called for both Case and Task
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value={"list": []})

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp

        await espo_service.get_tasks_async(force_refresh=True)

        # 1 for User map, 1 for Case, 1 for Task
        # (Actually, User map is fetched once, then Case and Task in gather)
        urls = [call.args[0] for call in mock_get.call_args_list]
        assert any("api/v1/User" in url for url in urls)
        assert any("api/v1/Case" in url for url in urls)
        assert any("api/v1/Task" in url for url in urls)
