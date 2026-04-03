from http import HTTPStatus
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest
from django.core.cache import cache

from task_dashboard.services.zammad import ZammadService


@pytest.fixture
def zammad_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://zammad.example.com"
    config.api_token = "test-token"  # noqa: S105
    config.name = "Zammad Test"
    return config


@pytest.fixture
def zammad_service(zammad_config):
    return ZammadService(zammad_config)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.anyio
async def test_get_user_map_pagination(zammad_service):
    # Page 1 (full)
    page1 = [
        {"id": i, "email": f"u{i}@ex.com", "login": f"u{i}"} for i in range(1, 251)
    ]
    # Page 2 (last)
    page2 = [{"id": 251, "email": "u251@ex.com", "login": "u251"}]

    async def mock_get(url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        if "api/v1/users" in url:
            if page == 1:
                resp.json = MagicMock(return_value=page1)
            elif page == 2:  # noqa: PLR2004
                resp.json = MagicMock(return_value=page2)
            else:
                resp.json = MagicMock(return_value=[])
        return resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get

        async with httpx.AsyncClient() as client:
            user_map = await zammad_service._get_user_map(client)  # noqa: SLF001

        assert len(user_map) == 251  # noqa: PLR2004
        assert user_map[1]["email"] == "u1@ex.com"
        assert user_map[251]["email"] == "u251@ex.com"


@pytest.mark.anyio
async def test_fetch_all_tasks_pagination(zammad_service):
    # Page 1 (full, 100 tickets)
    page1 = [{"id": i, "title": f"Ticket {i}"} for i in range(1, 101)]
    # Page 2 (last, 5 tickets)
    page2 = [{"id": 101 + i, "title": f"Ticket {101 + i}"} for i in range(5)]

    async def mock_get(url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.raise_for_status = MagicMock()
        if "api/v1/tickets" in url:
            if page == 1:
                resp.json = MagicMock(
                    return_value={"tickets": page1} if page == 1 else page1
                )
            elif page == 2:  # noqa: PLR2004
                resp.json = MagicMock(return_value=page2)
            else:
                resp.json = MagicMock(return_value=[])
        return resp

    # To handle the dict/list variation in Zammad service
    async def mock_get_flexible(url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.raise_for_status = MagicMock()
        if page == 1:
            resp.json = MagicMock(return_value={"tickets": page1})
        elif page == 2:  # noqa: PLR2004
            resp.json = MagicMock(return_value=page2)
        else:
            resp.json = MagicMock(return_value=[])
        return resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get_flexible

        async with httpx.AsyncClient() as client:
            tasks = await zammad_service._fetch_all_tasks_async(client)  # noqa: SLF001

        assert len(tasks) == 105  # noqa: PLR2004
        assert tasks[0]["id"] == 1
        assert tasks[104]["id"] == 105  # noqa: PLR2004
