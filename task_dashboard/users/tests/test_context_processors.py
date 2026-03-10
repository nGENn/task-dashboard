from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from task_dashboard.context_processors import MAX_HEALTHY_LATENCY_MS
from task_dashboard.context_processors import system_status
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.tests.factories import UserFactory


@pytest.mark.django_db
class TestSystemStatusContextProcessor:
    def test_unauthenticated_user(self, rf: RequestFactory):
        request = rf.get("/")
        request.user = AnonymousUser()
        context = system_status(request)
        assert context == {}

    def test_no_active_services(self, user: UserFactory, rf: RequestFactory):
        request = rf.get("/")
        request.user = user
        context = system_status(request)
        assert context["global_system_status"]["state"] == "No Services"
        assert context["services_status"] == []

    @patch("task_dashboard.context_processors.ErambaService")
    def test_degraded_service(self, mock_eramba, user: UserFactory, rf: RequestFactory):
        # Setup active service
        ServiceConfiguration.objects.create(
            name="Eramba",
            service_type="eramba",
            is_active=True,
        )

        # Mock health check to return high latency
        mock_instance = mock_eramba.return_value
        mock_instance.check_health.return_value = {
            "name": "Eramba",
            "status": "online",
            "latency": MAX_HEALTHY_LATENCY_MS + 100,
            "error": None,
        }

        request = rf.get("/")
        request.user = user
        context = system_status(request)

        # Global state should be Degraded
        assert context["global_system_status"]["state"] == "Degraded"
        assert context["global_system_status"]["color"] == "warning"

        # Individual service should be marked as degraded
        service_status = context["services_status"][0]
        assert service_status["name"] == "Eramba"
        assert service_status["status"] == "degraded"
        assert service_status["latency"] == MAX_HEALTHY_LATENCY_MS + 100

    @patch("task_dashboard.context_processors.ErambaService")
    def test_healthy_service(self, mock_eramba, user: UserFactory, rf: RequestFactory):
        # Setup active service
        ServiceConfiguration.objects.create(
            name="Eramba",
            service_type="eramba",
            is_active=True,
        )

        # Mock health check to return low latency
        mock_instance = mock_eramba.return_value
        mock_instance.check_health.return_value = {
            "name": "Eramba",
            "status": "online",
            "latency": MAX_HEALTHY_LATENCY_MS - 100,
            "error": None,
        }

        request = rf.get("/")
        request.user = user
        context = system_status(request)

        # Global state should be Healthy
        assert context["global_system_status"]["state"] == "Healthy"
        assert context["global_system_status"]["color"] == "success"

        # Individual service should be marked as online
        service_status = context["services_status"][0]
        assert service_status["name"] == "Eramba"
        assert service_status["status"] == "online"
