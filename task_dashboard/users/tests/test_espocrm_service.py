from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
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


@pytest.fixture(autouse=True)
def mock_global_setting():
    with patch(
        "task_dashboard.services.espocrm.GlobalSetting.objects.afirst",
        new_callable=AsyncMock,
    ) as mock:
        mock_setting = MagicMock()
        mock_setting.company_name = "Internal"
        mock.return_value = mock_setting
        yield mock


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
    user_data: dict[str, list[Any]] = {"list": []}

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


@pytest.mark.anyio
async def test_fetch_entity_pagination(espo_service):
    # Mock Cases with 2 pages
    page1 = [{"id": f"c{i}", "name": f"Case {i}"} for i in range(1, 101)]
    page2 = [{"id": "c101", "name": "Case 101"}]

    async def mock_get(url, **kwargs):
        offset = kwargs.get("params", {}).get("offset")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.raise_for_status = MagicMock()
        if offset == 0:
            resp.json = MagicMock(return_value={"list": page1})
        elif offset == 100:  # noqa: PLR2004
            resp.json = MagicMock(return_value={"list": page2})
        else:
            resp.json = MagicMock(return_value={"list": []})
        return resp

    ctx: dict[str, Any] = {"target": [], "user_map": {}, "company_name": "TestCorp"}
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get

        async with httpx.AsyncClient() as client:
            await espo_service._fetch_entity(  # noqa: SLF001
                client, "https://espo.example.com/api/v1/Case", "Case", {}, ctx
            )

        assert len(ctx["target"]) == 101  # noqa: PLR2004
        assert ctx["target"][0]["title"] == "Case 1"
        assert ctx["target"][100]["title"] == "Case 101"


@pytest.mark.anyio
async def test_get_tasks_async_uses_global_setting_for_customer_fallback(espo_service):
    # Mock GlobalSetting
    mock_global_setting = MagicMock()
    mock_global_setting.company_name = "My Test Company"

    case_data = {
        "list": [
            {
                "id": "case-1",
                "number": 1,
                "name": "Case with Customer",
                "status": "New",
                "accountName": "Existing Customer",
            },
            {
                "id": "case-2",
                "number": 2,
                "name": "Case without Customer",
                "status": "New",
                "accountName": None,
            },
        ]
    }

    with patch(
        "task_dashboard.services.espocrm.GlobalSetting.objects.afirst",
        new_callable=AsyncMock,
    ) as mock_afirst:
        mock_afirst.return_value = mock_global_setting

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json = MagicMock(
                side_effect=[
                    {"list": []},  # User map
                    case_data,  # Cases
                    {"list": []},  # Tasks
                ]
            )
            mock_get.return_value = mock_resp

            tasks = await espo_service.get_tasks_async(force_refresh=True)

            assert len(tasks) == 2  # noqa: PLR2004
            # First task should keep its customer
            assert tasks[0]["customer"] == "Existing Customer"
            # Second task should use the global fallback
            assert tasks[1]["customer"] == "My Test Company"
