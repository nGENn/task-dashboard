"""Item 6: per-owner Kimai team visibility — activity grant + stale revoke.

Exercises ``_sync_activities_async`` with a mocked KimaiClient so the team
grant/revoke decisions have coverage (they are never exercised by the other
suites and were not run against a live Kimai).
"""

from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync

from task_dashboard.kimai.models import KimaiSettings
from task_dashboard.kimai.tasks import _activity_comment
from task_dashboard.kimai.tasks import _sync_activities_async
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task


def _base_client() -> AsyncMock:
    client = AsyncMock()
    client.get_customers.return_value = []
    client.get_projects.return_value = []
    client.get_teams.return_value = []
    client.get_users.return_value = []
    client.create_customer.return_value = {"id": 1, "name": "Acme"}
    client.create_project.return_value = {"id": 7, "name": "Support", "comment": "c"}
    client.create_team.return_value = {"id": 33}
    client.get_activities.return_value = []
    client.create_activity.return_value = {"id": 500}
    return client


def _make_config_and_task(owner_email: str) -> ServiceConfiguration:
    config = ServiceConfiguration.objects.create(
        name="Zam", service_type="zammad", api_url="http://x", is_active=True
    )
    Task.objects.create(
        service=config,
        external_id="ZAM-1",
        title="Ticket",
        status="open",
        owner_email=owner_email,
    )
    return config


@pytest.mark.django_db(transaction=True)
def test_new_activity_granted_to_owner_team():
    config = _make_config_and_task("alice@example.com")
    client = _base_client()
    client.get_users.return_value = [{"id": 99, "email": "alice@example.com"}]

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    # A team named by the owner email is created (with the account as member)
    # and the new activity granted to it.
    client.create_team.assert_awaited_once()
    payload = client.create_team.await_args.args[0]
    assert payload["name"] == "Owner: alice@example.com"
    assert payload["members"] == [{"user": 99, "teamlead": True}]
    client.grant_team_activity.assert_awaited_once_with(33, 500)
    client.revoke_team_activity.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_owner_without_kimai_account_is_provisioned():
    # Kimai rejects memberless teams, so an owner with no Kimai account is
    # auto-provisioned a Kimai user, then gets a per-user team + restriction.
    config = _make_config_and_task("ghost@example.com")
    client = _base_client()  # get_users empty -> ghost has no account
    client.create_user.return_value = {"id": 77}

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    client.create_user.assert_awaited_once()
    user_payload = client.create_user.await_args.args[0]
    assert user_payload["email"] == "ghost@example.com"
    assert user_payload["username"] == "ghost@example.com"
    assert user_payload.get("plainPassword")  # random password set
    client.create_team.assert_awaited_once()
    team_payload = client.create_team.await_args.args[0]
    assert team_payload["members"] == [{"user": 77, "teamlead": True}]
    client.grant_team_activity.assert_awaited_once_with(33, 500)


@pytest.mark.django_db(transaction=True)
def test_stale_owner_team_revoked_on_reassignment():
    config = _make_config_and_task("alice@example.com")
    comment = _activity_comment(config.id, "ZAM-1")

    client = _base_client()
    client.get_users.return_value = [{"id": 99, "email": "alice@example.com"}]
    # Two owner teams already exist: 33 = alice (current), 44 = a former owner.
    client.get_teams.return_value = [
        {"id": 33, "name": "Owner: alice@example.com"},
        {"id": 44, "name": "Owner: bob@example.com"},
    ]
    client.get_activities.return_value = [
        {"id": 500, "comment": comment, "name": "Ticket", "visible": True}
    ]
    # The activity is still restricted to the former owner's team (44).
    client.get_activity.return_value = {"id": 500, "teams": [{"id": 44}]}

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    # Alice's team granted; the stale managed team revoked.
    client.grant_team_activity.assert_awaited_once_with(33, 500)
    client.revoke_team_activity.assert_awaited_once_with(44, 500)
