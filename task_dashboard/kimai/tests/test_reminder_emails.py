"""Tests for send_kimai_reminder_emails workday/holiday gating."""

from datetime import UTC
from datetime import date
from datetime import datetime
from unittest.mock import patch

import pytest
from django.core.cache import cache

from task_dashboard.kimai.models import KimaiSettings
from task_dashboard.kimai.tasks import send_kimai_reminder_emails
from task_dashboard.users.models import TaskOwner
from task_dashboard.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

MONDAY = datetime(2026, 6, 15, 7, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 6, 13, 7, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def settings_enabled(db):
    return KimaiSettings.objects.create(
        pk=1,
        reminder_enabled=True,
        reminder_email_enabled=True,
        grace_period_days=3,
    )


@pytest.fixture
def behind_owner(db):
    owner = TaskOwner.objects.create(email="behind@example.com", name="Behind Person")
    cache.set(
        f"kimai_reminder:owner:{owner.pk}",
        {"days_behind": 5, "never_booked": False, "ts": "2026-06-12T00:00:00+00:00"},
        timeout=3600,
    )
    return owner


def _run_at(now: datetime, holidays: frozenset = frozenset()) -> int:
    with (
        patch("task_dashboard.kimai.tasks.datetime") as mock_dt,
        patch("task_dashboard.kimai.tasks.get_public_holidays", return_value=holidays),
    ):
        mock_dt.now.return_value = now
        return send_kimai_reminder_emails()


class TestSendReminderEmailsWorkdayGate:
    def test_sends_on_workday(self, settings_enabled, behind_owner, mailoutbox):
        sent = _run_at(MONDAY)
        assert sent == 1
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["behind@example.com"]

    def test_skips_weekend_with_default_working_days(
        self, settings_enabled, behind_owner, mailoutbox
    ):
        sent = _run_at(SATURDAY)
        assert sent == 0
        assert len(mailoutbox) == 0

    def test_sends_on_saturday_when_owner_works_saturdays(
        self, settings_enabled, behind_owner, mailoutbox
    ):
        behind_owner.user = UserFactory(working_days=[0, 1, 2, 3, 4, 5])
        behind_owner.save()

        sent = _run_at(SATURDAY)
        assert sent == 1
        assert len(mailoutbox) == 1

    def test_skips_public_holiday(self, settings_enabled, behind_owner, mailoutbox):
        sent = _run_at(MONDAY, holidays=frozenset({date(2026, 6, 15)}))
        assert sent == 0
        assert len(mailoutbox) == 0

    def test_skips_owner_within_grace(self, settings_enabled, mailoutbox):
        owner = TaskOwner.objects.create(email="ok@example.com", name="On Track")
        cache.set(
            f"kimai_reminder:owner:{owner.pk}",
            {
                "days_behind": 1,
                "never_booked": False,
                "ts": "2026-06-12T00:00:00+00:00",
            },
            timeout=3600,
        )
        sent = _run_at(MONDAY)
        assert sent == 0
        assert len(mailoutbox) == 0

    def test_sends_at_most_once_per_day(
        self, settings_enabled, behind_owner, mailoutbox
    ):
        assert _run_at(MONDAY) == 1
        assert _run_at(MONDAY) == 0
        assert len(mailoutbox) == 1
