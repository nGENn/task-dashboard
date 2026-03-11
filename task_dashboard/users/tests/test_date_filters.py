import pytest
from django.utils import timezone
from datetime import timedelta
from task_dashboard.users.models import Task, ServiceConfiguration
from task_dashboard.users.views import DashboardView

@pytest.mark.django_db
def test_date_range_filters(user, rf):
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="FULL",
    )

    now = timezone.now()
    
    # Task 1: Created yesterday, updated today, due tomorrow
    t1 = Task.objects.create(
        external_id="T1",
        title="Task 1",
        status="open",
        service=service,
        created_at=now - timedelta(days=1),
        updated_at=now,
        due_date=now + timedelta(days=1),
    )
    
    # Task 2: Created 10 days ago, updated 5 days ago, due yesterday
    t2 = Task.objects.create(
        external_id="T2",
        title="Task 2",
        status="open",
        service=service,
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=5),
        due_date=now - timedelta(days=1),
    )

    def get_tasks(query_params):
        request = rf.get(f"/?view=all&{query_params}")
        request.user = user
        view = DashboardView()
        view.request = request
        context = view.get_context_data()
        return [t.external_id for t in context["tasks"].object_list]

    # Test created_range (date_range)
    start = (now - timedelta(days=2)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    task_ids = get_tasks(f"date_range={start} to {end}")
    assert "T1" in task_ids
    assert "T2" not in task_ids

    # Test updated_range
    start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    end = (now - timedelta(days=4)).strftime("%Y-%m-%d")
    task_ids = get_tasks(f"updated_range={start} to {end}")
    assert "T1" not in task_ids
    assert "T2" in task_ids

    # Test due_range
    start = now.strftime("%Y-%m-%d")
    end = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    task_ids = get_tasks(f"due_range={start} to {end}")
    assert "T1" in task_ids
    assert "T2" not in task_ids
