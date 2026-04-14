import pytest
from django.urls import reverse
from task_dashboard.users.models import Task, ServiceConfiguration
from task_dashboard.users.tests.factories import UserFactory

@pytest.mark.django_db
class TestOpenPerspective:
    def test_open_perspective_filters_by_open_status(self, client):
        user = UserFactory(email="test@example.com", name="Test User")
        client.force_login(user)
        
        service = ServiceConfiguration.objects.create(
            name="Test Service",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        # Create an open task
        Task.objects.create(external_id="T1", status="open", service=service, title="Open Task", owner="Someone")
        # Create a pending task
        Task.objects.create(external_id="T2", status="pending", service=service, title="Pending Task", owner="Someone")
        # Create a resolved task
        Task.objects.create(external_id="T3", status="resolved", service=service, title="Resolved Task", owner="Someone")
        
        url = reverse("open_tasks")
        response = client.get(url)
        
        assert response.status_code == 200
        tasks = response.context["tasks"]
        # Should only contain the open task
        assert len(tasks) == 1
        assert tasks[0].status == "open"
        assert tasks[0].title == "Open Task"
        
        # Verify applied filters in context
        assert response.context["applied_filters"]["states"] == ["open"]

    def test_open_perspective_stats(self, client):
        user = UserFactory(email="test@example.com", name="Test User")
        client.force_login(user)
        
        service = ServiceConfiguration.objects.create(
            name="Test Service",
            service_type="zammad",
            is_active=True,
            default_access_level="FULL",
        )
        # Assigned tasks
        Task.objects.create(external_id="T4", status="open", service=service, owner="Alice")
        Task.objects.create(external_id="T5", status="open", service=service, owner="Bob")
        Task.objects.create(external_id="T6", status="pending", service=service, owner="Charlie")
        # Unassigned task (Both owner and owner_email must be in UNASSIGNED_MARKERS)
        Task.objects.create(external_id="T7", status="pending", service=service, owner="", owner_email="")
        
        url = reverse("open_tasks")
        response = client.get(url)
        
        stats = response.context["stats"]
        assert stats["open"] == 2
        assert stats["pending"] == 2
        assert stats["unassigned"] == 1
        assert stats["total"] == 4
