from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache

from task_dashboard.services.openproject import OpenProjectService


@pytest.fixture
def op_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://openproject.example.com"
    config.api_token = "test-token"  # noqa: S105
    config.name = "OpenProject Test"
    return config


@pytest.fixture
def op_service(op_config):
    return OpenProjectService(op_config)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.anyio
async def test_get_user_map_pagination(op_service):
    # Create 100 users for page 1 to trigger pagination
    elements_page1 = [
        {"id": i, "email": f"u{i}@ex.com", "login": f"u{i}"} for i in range(1, 101)
    ]
    page1_full = {"_embedded": {"elements": elements_page1}}

    # Page 2 has 1 user
    page2_last = {
        "_embedded": {
            "elements": [{"id": 101, "email": "u101@ex.com", "login": "u101"}]
        }
    }

    async def mock_get_paginated(url, **kwargs):
        offset = kwargs.get("params", {}).get("offset")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        if offset == 1:
            resp.json = MagicMock(return_value=page1_full)
        elif offset == 2:  # noqa: PLR2004
            resp.json = MagicMock(return_value=page2_last)
        else:
            resp.json = MagicMock(return_value={"_embedded": {"elements": []}})
        return resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get_paginated

        async with httpx.AsyncClient() as client:
            user_map = await op_service._get_user_map(client)  # noqa: SLF001

        assert len(user_map) == 101  # noqa: PLR2004
        assert user_map[1] == "u1@ex.com"
        assert user_map[100] == "u100@ex.com"
        assert user_map[101] == "u101@ex.com"
        assert mock_get_call.call_count == 2  # noqa: PLR2004


@pytest.mark.anyio
async def test_get_user_map_placeholder_fallback(op_service):
    data = {
        "_embedded": {
            "elements": [
                {"id": 42, "email": None, "login": "noemailuser"},
            ]
        }
    }

    mock_resp = MagicMock()
    mock_resp.status_code = HTTPStatus.OK
    mock_resp.json = MagicMock(return_value=data)

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        async with httpx.AsyncClient() as client:
            user_map = await op_service._get_user_map(client)  # noqa: SLF001

        assert user_map[42] == "noemailuser@placeholder"


@pytest.mark.anyio
async def test_get_user_map_forbidden(op_service):
    mock_resp = MagicMock()
    mock_resp.status_code = HTTPStatus.FORBIDDEN

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        async with httpx.AsyncClient() as client:
            user_map = await op_service._get_user_map(client)  # noqa: SLF001

        assert user_map == {}


@pytest.mark.anyio
async def test_fetch_work_packages_pagination(op_service):
    # Mock work packages with 2 pages
    page1 = [{"id": i, "subject": f"Task {i}"} for i in range(1, 101)]
    page2 = [{"id": 101, "subject": "Task 101"}]

    async def mock_get(url, **kwargs):
        offset = kwargs.get("params", {}).get("offset")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.raise_for_status = MagicMock()
        if offset == 1:
            resp.json = MagicMock(return_value={"_embedded": {"elements": page1}})
        elif offset == 2:  # noqa: PLR2004
            resp.json = MagicMock(return_value={"_embedded": {"elements": page2}})
        else:
            resp.json = MagicMock(return_value={"_embedded": {"elements": []}})
        return resp

    tasks: list[dict[str, Any]] = []
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get

        async with httpx.AsyncClient() as client:
            await op_service._fetch_work_packages(client, tasks, {}, "TestCorp")  # noqa: SLF001

        assert len(tasks) == 101  # noqa: PLR2004
        assert tasks[0]["title"] == "Task 1"
        assert tasks[100]["title"] == "Task 101"
