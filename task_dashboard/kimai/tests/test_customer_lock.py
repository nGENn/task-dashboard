"""M2: customer get-or-create is atomic across parallel per-config syncs."""

from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync

from task_dashboard.kimai.tasks import _ensure_customer
from task_dashboard.kimai.tasks import _norm_customer_key


def test_fast_path_returns_known_customer_without_api_calls():
    client = AsyncMock()
    existing = {"id": 1, "name": "Acme"}
    by_name = {_norm_customer_key("Acme"): existing}

    result = async_to_sync(_ensure_customer)(client, "Acme", by_name, None)

    assert result is existing
    client.get_customers.assert_not_called()
    client.create_customer.assert_not_called()


def test_reuses_customer_created_by_another_config():
    """The local map missed it (created after our initial fetch); the re-query
    under the lock finds it, so we never create a duplicate."""
    client = AsyncMock()
    remote = {"id": 9, "name": "Test"}
    client.get_customers.return_value = [remote]
    by_name: dict = {}

    result = async_to_sync(_ensure_customer)(client, "Test", by_name, None)

    assert result is remote
    client.create_customer.assert_not_called()
    assert by_name[_norm_customer_key("Test")] is remote


def test_creates_when_truly_absent():
    client = AsyncMock()
    client.get_customers.return_value = []
    created = {"id": 5, "name": "New Co"}
    client.create_customer.return_value = created
    by_name: dict = {}

    result = async_to_sync(_ensure_customer)(client, "New Co", by_name, None)

    assert result is created
    client.create_customer.assert_awaited_once()
    assert by_name[_norm_customer_key("New Co")] is created
