"""Tests for kimai_reminder context processor (V5, V7, V9)."""

from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory

from task_dashboard.context_processors import kimai_reminder
from task_dashboard.kimai.models import KimaiSettings
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def kimai_config(db):
    return ServiceConfiguration.objects.create(
        name="Kimai",
        service_type="kimai",
        api_url="https://kimai.example.com",
        is_active=True,
    )


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestKimaiReminderContextProcessor:
    def test_unauthenticated_returns_empty(self, rf: RequestFactory):
        request = rf.get("/")
        request.user = AnonymousUser()
        assert kimai_reminder(request) == {}

    def test_no_cache_returns_none_reminder(self, user, rf: RequestFactory):
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx == {"kimai_reminder": None}

    def test_zero_days_behind_returns_none(self, user, rf: RequestFactory):
        cache.set(f"kimai_reminder:{user.pk}", {"days_behind": 0, "ts": "2025-01-01"})
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx["kimai_reminder"] is None

    def test_within_grace_period_returns_none(self, user, rf: RequestFactory):
        # default grace_period_days=3; days=3 is within grace → no banner
        KimaiSettings.objects.create(pk=1, grace_period_days=3)
        cache.set(f"kimai_reminder:{user.pk}", {"days_behind": 3, "ts": "2025-01-01"})
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx["kimai_reminder"] is None

    def test_behind_returns_data(self, user, kimai_config, rf: RequestFactory):
        # days=4 exceeds default grace_period_days=3 → banner shown
        KimaiSettings.objects.create(pk=1, grace_period_days=3)
        days = 4
        data = {"days_behind": days, "ts": "2025-01-01"}
        cache.set(f"kimai_reminder:{user.pk}", data)
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx["kimai_reminder"]["days_behind"] == days
        assert ctx["kimai_reminder"]["kimai_base_url"] == "https://kimai.example.com"

    def test_behind_no_kimai_config_returns_empty_url(self, user, rf: RequestFactory):
        KimaiSettings.objects.create(pk=1, grace_period_days=3)
        days = 4
        data = {"days_behind": days, "ts": "2025-01-01"}
        cache.set(f"kimai_reminder:{user.pk}", data)
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx["kimai_reminder"]["days_behind"] == days
        assert ctx["kimai_reminder"]["kimai_base_url"] == ""

    def test_never_booked_shows_banner(self, user, kimai_config, rf: RequestFactory):
        data = {"days_behind": 0, "never_booked": True, "ts": "2025-01-01"}
        cache.set(f"kimai_reminder:{user.pk}", data)
        request = rf.get("/")
        request.user = user
        ctx = kimai_reminder(request)
        assert ctx["kimai_reminder"] is not None
        assert ctx["kimai_reminder"]["never_booked"] is True

    def test_no_api_call_in_request_path(self, user, rf: RequestFactory):
        """V5: context processor must not call Kimai API."""
        KimaiSettings.objects.create(pk=1, grace_period_days=3)
        reminder_data = {"days_behind": 5, "ts": "2025-01-01"}
        with patch("task_dashboard.kimai.client.KimaiClient") as mock_client:
            cache.set(f"kimai_reminder:{user.pk}", reminder_data)
            request = rf.get("/")
            request.user = user
            kimai_reminder(request)
            mock_client.assert_not_called()
