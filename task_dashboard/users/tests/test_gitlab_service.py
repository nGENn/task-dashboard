import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from http import HTTPStatus
from django.core.cache import cache
from task_dashboard.services.gitlab import GitLabService

@pytest.fixture
def gl_config():
    config = MagicMock()
    config.id = 1
    config.api_url = "https://gitlab.example.com"
    config.api_token = "test-token"
    config.name = "GitLab Test"
    return config

@pytest.fixture
def gl_service(gl_config):
    return GitLabService(gl_config)

@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()

@pytest.mark.anyio
async def test_get_user_map_pagination(gl_service):
    # Page 1 (full)
    page1 = [{"id": i, "email": f"u{i}@ex.com"} for i in range(1, 101)]
    # Page 2 (last)
    page2 = [{"id": 101, "email": "u101@ex.com"}]
    
    async def mock_get(url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        if "api/v4/users" in url:
            if page == 1:
                resp.json = MagicMock(return_value=page1)
            elif page == 2:
                resp.json = MagicMock(return_value=page2)
            else:
                resp.json = MagicMock(return_value=[])
        return resp

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get
        
        async with httpx.AsyncClient() as client:
            user_map = await gl_service._get_user_map(client)
            
        assert len(user_map) == 101
        assert user_map[1] == "u1@ex.com"
        assert user_map[101] == "u101@ex.com"
        assert mock_get_call.call_count == 2

@pytest.mark.anyio
async def test_fetch_and_normalize_pagination(gl_service):
    # Mock issues with 2 pages
    page1 = [{"iid": i, "title": f"Issue {i}", "id": i} for i in range(1, 101)]
    page2 = [{"iid": 101, "title": "Issue 101", "id": 101}]
    
    async def mock_get(url, **kwargs):
        page = kwargs.get("params", {}).get("page")
        resp = MagicMock()
        resp.status_code = HTTPStatus.OK
        resp.raise_for_status = MagicMock()
        if "api/v4/issues" in url:
            if page == 1:
                resp.json = MagicMock(return_value=page1)
            elif page == 2:
                resp.json = MagicMock(return_value=page2)
            else:
                resp.json = MagicMock(return_value=[])
        return resp

    ctx = {"target": [], "user_map": {}, "company_name": "TestCorp"}
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get_call:
        mock_get_call.side_effect = mock_get
        
        async with httpx.AsyncClient() as client:
            await gl_service._fetch_and_normalize(
                client, "https://gitlab.example.com/api/v4/issues", "Issue", ctx
            )
            
        assert len(ctx["target"]) == 101
        assert ctx["target"][0]["title"] == "Issue 1"
        assert ctx["target"][100]["title"] == "Issue 101"
