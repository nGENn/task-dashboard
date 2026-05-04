from datetime import timedelta
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from django.utils import timezone

from task_dashboard.users.models import ExternalGroup
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.tasks import fetch_all_tasks_task
from task_dashboard.users.tasks import fetch_service_tasks


@pytest.fixture
def service_config(db):
    return ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        api_url="http://test.com",
    )


@pytest.mark.django_db
def test_fetch_service_tasks_success(service_config):
    # Mock data from service
    task_data = [
        {
            "id": "1",
            "title": "Task 1",
            "status": "open",
            "priority": "high",
            "group": "Support",
            "updated_at": timezone.now().isoformat(),
        },
        {
            "id": "2",
            "title": "Task 2",
            "status": "pending",
            "priority": "low",
            "group": "Sales",
            "updated_at": (timezone.now() - timedelta(hours=1)).isoformat(),
        },
    ]

    with patch("task_dashboard.users.tasks.SERVICE_CLASSES") as mock_services:
        mock_service_instance = MagicMock()
        mock_service_instance.get_tasks.return_value = task_data
        mock_services.get.return_value = lambda config: mock_service_instance

        count = fetch_service_tasks(service_config.id)

        assert count == 2  # noqa: PLR2004
        assert Task.objects.filter(service=service_config).count() == 2  # noqa: PLR2004
        assert ExternalGroup.objects.filter(origin=service_config.name).count() == 2  # noqa: PLR2004

        # Verify specific fields
        task1 = Task.objects.get(external_id="1", service=service_config)
        assert task1.title == "Task 1"
        assert task1.group == "Support"
        assert task1.service_group is not None
        assert task1.service_group.name == "Support"


@pytest.mark.django_db
def test_fetch_service_tasks_update_conflicts(service_config):
    # Existing task
    existing_task = Task.objects.create(
        service=service_config,
        external_id="1",
        title="Old Title",
        status="open",
        updated_at=timezone.now() - timedelta(days=1),
    )

    # Mock data with update
    task_data = [
        {
            "id": "1",
            "title": "New Title",
            "status": "closed",
            "updated_at": timezone.now().isoformat(),
        }
    ]

    with patch("task_dashboard.users.tasks.SERVICE_CLASSES") as mock_services:
        mock_service_instance = MagicMock()
        mock_service_instance.get_tasks.return_value = task_data
        mock_services.get.return_value = lambda config: mock_service_instance

        fetch_service_tasks(service_config.id)

        existing_task.refresh_from_db()
        assert existing_task.title == "New Title"
        assert existing_task.status == "closed"


@pytest.mark.django_db
def test_fetch_service_tasks_pruning(service_config):
    # Two existing tasks
    Task.objects.create(service=service_config, external_id="1", title="T1")
    Task.objects.create(service=service_config, external_id="2", title="T2")

    # Mock data with only one task
    task_data = [{"id": "1", "title": "T1"}]

    with patch("task_dashboard.users.tasks.SERVICE_CLASSES") as mock_services:
        mock_service_instance = MagicMock()
        mock_service_instance.get_tasks.return_value = task_data
        mock_services.get.return_value = lambda config: mock_service_instance

        fetch_service_tasks(service_config.id)

        assert Task.objects.filter(service=service_config).count() == 1
        assert not Task.objects.filter(external_id="2").exists()


@pytest.mark.django_db
def test_fetch_service_tasks_service_error(service_config, caplog):
    with patch("task_dashboard.users.tasks.SERVICE_CLASSES") as mock_services:
        mock_service_instance = MagicMock()
        mock_service_instance.get_tasks.side_effect = Exception("API Down")
        mock_services.get.return_value = lambda config: mock_service_instance

        with caplog.at_level("ERROR"):
            count = fetch_service_tasks(service_config.id)

        assert count == 0
        assert "Error fetching tasks for service" in caplog.text


@pytest.mark.django_db
def test_fetch_all_tasks_task_dispatch(db):
    ServiceConfiguration.objects.create(
        name="S1", is_active=True, service_type="zammad"
    )
    ServiceConfiguration.objects.create(
        name="S2", is_active=True, service_type="gitlab"
    )
    ServiceConfiguration.objects.create(
        name="S3", is_active=False, service_type="espocrm"
    )

    with patch("task_dashboard.users.tasks.async_task") as mock_async:
        count = fetch_all_tasks_task()

        assert count == 2  # noqa: PLR2004
        assert mock_async.call_count == 2  # noqa: PLR2004
