"""Tests for activity sync helpers (V3, V12)."""

from unittest.mock import AsyncMock

import pytest
from asgiref.sync import async_to_sync

from task_dashboard.kimai.models import KimaiSettings
from task_dashboard.kimai.tasks import _activity_comment
from task_dashboard.kimai.tasks import _activity_comment_display
from task_dashboard.kimai.tasks import _canonical_emails
from task_dashboard.kimai.tasks import _parse_activity_comment
from task_dashboard.kimai.tasks import _sync_activities_async
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task


class TestActivityCommentFormat:
    """V3: activity.comment = "{service_config_id}:{external_task_id}" """

    def test_format(self):
        assert _activity_comment(42, "ZAM-123") == "42:ZAM-123"

    def test_format_numeric_external(self):
        assert _activity_comment(1, "456") == "1:456"

    def test_parse_valid(self):
        assert _parse_activity_comment("42:ZAM-123") == (42, "ZAM-123")

    def test_parse_task_id_with_colon(self):
        # External task IDs with colons: only first separator is split
        assert _parse_activity_comment("5:GL-I-10:extra") == (5, "GL-I-10:extra")

    def test_parse_ignores_url_line(self):
        # The display comment carries a URL on line 2 — the parser keys off
        # line 1 only, and the URL's own colons never reach the split.
        comment = "42:ZAM-123\nhttps://zammad.example/#ticket/zoom/9"
        assert _parse_activity_comment(comment) == (42, "ZAM-123")

    def test_parse_none(self):
        assert _parse_activity_comment(None) is None

    def test_parse_empty(self):
        assert _parse_activity_comment("") is None

    def test_parse_no_separator(self):
        assert _parse_activity_comment("no-colon") is None

    def test_parse_non_numeric_config_id(self):
        assert _parse_activity_comment("abc:task-1") is None

    def test_roundtrip(self):
        config_id, ext_id = 7, "OP-99"
        parsed = _parse_activity_comment(_activity_comment(config_id, ext_id))
        assert parsed == (config_id, ext_id)

    def test_different_config_ids_are_distinct(self):
        """V3: comment encodes config_id so no cross-service collision."""
        c1 = _activity_comment(1, "TASK-1")
        c2 = _activity_comment(2, "TASK-1")
        assert c1 != c2
        assert _parse_activity_comment(c1) == (1, "TASK-1")
        assert _parse_activity_comment(c2) == (2, "TASK-1")


class TestActivityCommentDisplay:
    """The on-Kimai comment: matching key on line 1, source URL below."""

    def test_with_url(self):
        assert (
            _activity_comment_display(42, "ZAM-123", "https://z/9")
            == "42:ZAM-123\nhttps://z/9"
        )

    def test_without_url_is_bare_key(self):
        # No trailing newline so the idempotency check never re-patches.
        assert _activity_comment_display(42, "ZAM-123", None) == "42:ZAM-123"
        assert _activity_comment_display(42, "ZAM-123", "") == "42:ZAM-123"

    def test_display_roundtrips_through_parser(self):
        comment = _activity_comment_display(7, "OP-99", "https://op/work_packages/99")
        assert _parse_activity_comment(comment) == (7, "OP-99")


class TestCanonicalEmails:
    """The owner-team chokepoint: address variants collapse to one team/user."""

    def test_variants_collapse_to_one(self):
        """The reported bug: two variants of one person → a single email."""
        m = {"m.handsche@ngenn.net": "handsche@ngenn.net"}
        assert _canonical_emails("m.handsche@ngenn.net, handsche@ngenn.net", m) == [
            "handsche@ngenn.net"
        ]

    def test_unmapped_passthrough(self):
        assert _canonical_emails("alice@example.com", {}) == ["alice@example.com"]

    def test_empty(self):
        assert _canonical_emails("", {}) == []
        assert _canonical_emails(None, {}) == []

    def test_distinct_owners_sorted_and_deduped(self):
        assert _canonical_emails("b@x.com, a@x.com, b@x.com", {}) == [
            "a@x.com",
            "b@x.com",
        ]


def _sync_client() -> AsyncMock:
    client = AsyncMock()
    client.get_customers.return_value = []
    client.get_projects.return_value = []
    client.get_teams.return_value = []
    client.get_users.return_value = []
    client.create_customer.return_value = {"id": 1, "name": "Acme"}
    client.create_project.return_value = {"id": 7, "name": "Support", "comment": "c"}
    client.get_activities.return_value = []
    client.create_activity.return_value = {"id": _ACTIVITY_ID}
    return client


# A task URL exercising the colon-in-URL path; no owner_email so the team
# grant/revoke machinery stays out of these create/patch assertions.
_TASK_URL = "https://zammad.example/#ticket/zoom/9"
_ACTIVITY_ID = 500


def _make_url_config() -> ServiceConfiguration:
    config = ServiceConfiguration.objects.create(
        name="Zam", service_type="zammad", api_url="http://x", is_active=True
    )
    Task.objects.create(
        service=config,
        external_id="ZAM-1",
        title="Ticket",
        status="open",
        url=_TASK_URL,
        owner_email="",
    )
    return config


@pytest.mark.django_db(transaction=True)
def test_source_url_written_into_new_activity_comment():
    config = _make_url_config()
    client = _sync_client()

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    client.create_activity.assert_awaited_once()
    payload = client.create_activity.await_args.args[0]
    assert payload["comment"] == f"{config.id}:ZAM-1\n{_TASK_URL}"


@pytest.mark.django_db(transaction=True)
def test_second_sync_is_idempotent():
    """Back-to-back sync issues zero creates/patches — covers both the dedup
    key (URL-bearing comment still matches) and the re-patch guard."""
    config = _make_url_config()
    client = _sync_client()
    # Kimai already holds the activity with the exact name + URL-bearing comment.
    client.get_activities.return_value = [
        {
            "id": _ACTIVITY_ID,
            "comment": f"{config.id}:ZAM-1\n{_TASK_URL}",
            "name": "Ticket",
            "visible": True,
        }
    ]

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    client.create_activity.assert_not_awaited()
    client.patch_activity.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
def test_legacy_keyless_comment_gets_url_patched_in():
    """An activity synced before this change (bare key, no URL) is patched up
    to carry the URL — without creating a duplicate."""
    config = _make_url_config()
    client = _sync_client()
    client.get_activities.return_value = [
        {
            "id": _ACTIVITY_ID,
            "comment": f"{config.id}:ZAM-1",
            "name": "Ticket",
            "visible": True,
        }
    ]

    async_to_sync(_sync_activities_async)(client, config, KimaiSettings.load())

    client.create_activity.assert_not_awaited()
    client.patch_activity.assert_awaited_once()
    args = client.patch_activity.await_args.args
    assert args[0] == _ACTIVITY_ID
    assert args[1]["comment"] == f"{config.id}:ZAM-1\n{_TASK_URL}"
