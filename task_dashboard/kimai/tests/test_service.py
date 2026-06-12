import httpx
import pytest
from asgiref.sync import async_to_sync

from task_dashboard.kimai.service import KimaiService
from task_dashboard.users.models import ServiceConfiguration

pytestmark = pytest.mark.django_db


def _make_service(monkeypatch, ping):
    config = ServiceConfiguration.objects.create(
        name="Kimai",
        service_type="kimai",
        api_url="https://kimai.example.com",
        api_token="tok",  # noqa: S106
    )
    service = KimaiService(config)
    monkeypatch.setattr(service._client, "ping", ping)  # noqa: SLF001
    return service


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://kimai.example.com/api/version")
    response = httpx.Response(status_code, request=request)
    message = "err"
    return httpx.HTTPStatusError(message, request=request, response=response)


def test_kimai_is_not_a_task_source():
    config = ServiceConfiguration.objects.create(
        name="Kimai",
        service_type="kimai",
        api_url="https://kimai.example.com",
    )
    service = KimaiService(config)
    assert async_to_sync(service.get_tasks_async)() == []
    assert async_to_sync(service.get_single_task_async)(None) is None


def test_check_health_online(monkeypatch):
    async def ping():
        return {"version": "2.0"}

    service = _make_service(monkeypatch, ping)
    result = service.check_health()
    assert result["status"] == "online"
    assert result["name"] == "Kimai"


@pytest.mark.parametrize("status_code", [401, 403])
def test_check_health_auth_error(monkeypatch, status_code):
    async def ping():
        raise _http_status_error(status_code)

    service = _make_service(monkeypatch, ping)
    assert service.check_health()["status"] == "auth_error"


def test_check_health_offline_on_server_error(monkeypatch):
    async def ping():
        raise _http_status_error(500)

    service = _make_service(monkeypatch, ping)
    result = service.check_health()
    assert result["status"] == "offline"
    assert "error" in result


def test_check_health_offline_on_connect_error(monkeypatch):
    async def ping():
        message = "refused"
        raise httpx.ConnectError(message)

    service = _make_service(monkeypatch, ping)
    assert service.check_health()["status"] == "offline"
