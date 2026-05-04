import pytest
from django.contrib.auth.models import Group

from task_dashboard.users.models import ExternalGroup
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import ServicePermission
from task_dashboard.users.models import Task
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.rbac import get_rbac_q

pytestmark = pytest.mark.django_db


def test_rbac_none(user):
    """Verify that RBAC_NONE (default) results in no tasks being visible."""
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="NONE",
    )
    Task.objects.create(
        external_id="T1", title="Task 1", status="open", service=service
    )

    q = get_rbac_q(user)
    assert Task.objects.filter(q).count() == 0


def test_rbac_full(user):
    """Verify that RBAC_FULL grants access to all tasks in the service."""
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="FULL",
    )
    Task.objects.create(
        external_id="T1", title="Task 1", status="open", service=service
    )
    Task.objects.create(
        external_id="T2", title="Task 2", status="open", service=service
    )

    q = get_rbac_q(user)
    assert Task.objects.filter(q).count() == 2  # noqa: PLR2004


def test_rbac_own_email_match(user):
    """Verify that RBAC_OWN limits tasks to those owned by the user (email match)."""
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="OWN",
    )
    # Owned by user
    Task.objects.create(
        external_id="T1", title="My Task", owner_email=user.email, service=service
    )
    # Not owned by user
    Task.objects.create(
        external_id="T2",
        title="Other Task",
        owner_email="other@example.com",
        service=service,
    )
    # Unassigned
    Task.objects.create(
        external_id="T3", title="Unassigned Task", owner_email="", service=service
    )

    q = get_rbac_q(user)
    visible_tasks = Task.objects.filter(q)
    assert visible_tasks.count() == 1
    assert visible_tasks[0].external_id == "T1"


def test_rbac_own_name_match(user):
    """Verify that RBAC_OWN limits tasks to those owned by the user.

    Uses name token match.
    """
    user.name = "John Doe"
    user.save()

    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="OWN",
    )
    # Owned by user (name match)
    Task.objects.create(
        external_id="T1", title="My Task", owner="John Doe", service=service
    )
    # Not owned by user
    Task.objects.create(
        external_id="T2", title="Other Task", owner="Jane Smith", service=service
    )

    q = get_rbac_q(user)
    visible_tasks = Task.objects.filter(q)
    assert visible_tasks.count() == 1
    assert visible_tasks[0].external_id == "T1"


def test_rbac_limited(user):
    """Verify that RBAC_LIMITED grants access to own tasks AND unassigned tasks."""
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="LIMITED",
    )
    # Owned by user
    Task.objects.create(
        external_id="T1", title="My Task", owner_email=user.email, service=service
    )
    # Unassigned
    Task.objects.create(
        external_id="T2", title="Unassigned Task", owner_email="", service=service
    )
    # Owned by someone else
    Task.objects.create(
        external_id="T3",
        title="Other Task",
        owner_email="other@example.com",
        service=service,
    )

    q = get_rbac_q(user)
    visible_tasks = Task.objects.filter(q)
    assert visible_tasks.count() == 2  # noqa: PLR2004
    ids = {t.external_id for t in visible_tasks}
    assert "T1" in ids
    assert "T2" in ids
    assert "T3" not in ids


def test_group_permission_override_service_default(user):
    """Verify that a group-level ServicePermission overrides the service default."""
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="NONE",
    )
    group = Group.objects.create(name="Support")
    user.groups.add(group)

    ServicePermission.objects.create(
        django_group=group, service=service, access_level="FULL"
    )

    Task.objects.create(external_id="T1", title="Task 1", service=service)

    q = get_rbac_q(user)
    assert Task.objects.filter(q).count() == 1


def test_task_permission_override_service_permission(user):
    """Verify that a TaskPermission overrides ServicePermission.

    Targeting specific external groups.
    """
    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="NONE",
    )
    ext_group = ExternalGroup.objects.create(origin=service.name, name="Critical")

    group = Group.objects.create(name="Admins")
    user.groups.add(group)

    # Service permission is OWN
    ServicePermission.objects.create(
        django_group=group, service=service, access_level="OWN"
    )

    # Task permission for "Critical" group is FULL
    TaskPermission.objects.create(
        django_group=group, allowed_external_group=ext_group, access_level="FULL"
    )

    # Task in Critical group (not owned) -> should be visible due to FULL
    Task.objects.create(
        external_id="T1",
        title="Critical Task",
        service=service,
        group="Critical",
        service_group=ext_group,
    )
    # Task in other group (not owned) -> should NOT be visible due to OWN
    Task.objects.create(
        external_id="T2", title="Normal Task", service=service, group="Normal"
    )

    q = get_rbac_q(user)
    visible_tasks = Task.objects.filter(q)
    assert visible_tasks.count() == 1
    assert visible_tasks[0].external_id == "T1"


def test_rbac_unassigned_markers(user):
    """Verify that various unassigned markers are correctly identified as unassigned."""
    from task_dashboard.users.identity import UNASSIGNED_MARKERS

    service = ServiceConfiguration.objects.create(
        name="Test Service",
        service_type="zammad",
        is_active=True,
        default_access_level="LIMITED",
    )

    for i, marker in enumerate(UNASSIGNED_MARKERS):
        Task.objects.create(
            external_id=f"M{i}", title=f"Marker {marker}", owner=marker, service=service
        )

    q = get_rbac_q(user)
    assert Task.objects.filter(q).count() == len(UNASSIGNED_MARKERS)
