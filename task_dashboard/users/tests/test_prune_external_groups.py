from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.utils import timezone

from task_dashboard.users.models import ExternalGroup
from task_dashboard.users.models import TaskPermission
from task_dashboard.users.models import UserTaskPermission

pytestmark = pytest.mark.django_db


def _age(group: ExternalGroup, days: int) -> None:
    # last_seen is auto_now; .update() bypasses it so we can backdate.
    old = timezone.now() - timedelta(days=days)
    ExternalGroup.objects.filter(pk=group.pk).update(last_seen=old)


def test_prunes_stale_unreferenced_group():
    stale = ExternalGroup.objects.create(origin="Zammad", name="Old")
    _age(stale, 200)

    call_command("prune_external_groups", days=90)

    assert not ExternalGroup.objects.filter(pk=stale.pk).exists()


def test_keeps_recent_group():
    recent = ExternalGroup.objects.create(origin="Zammad", name="Fresh")
    # last_seen defaults to now via auto_now on create

    call_command("prune_external_groups", days=90)

    assert ExternalGroup.objects.filter(pk=recent.pk).exists()


def test_keeps_stale_group_referenced_by_taskpermission():
    referenced = ExternalGroup.objects.create(origin="Zammad", name="Guarded")
    _age(referenced, 200)
    dj_group = Group.objects.create(name="support")
    TaskPermission.objects.create(
        django_group=dj_group,
        allowed_external_group=referenced,
        access_level="FULL",
    )

    call_command("prune_external_groups", days=90)

    # Kept despite being stale — deleting it would CASCADE-delete the permission.
    assert ExternalGroup.objects.filter(pk=referenced.pk).exists()
    assert TaskPermission.objects.filter(allowed_external_group=referenced).exists()


def test_keeps_stale_group_referenced_by_usertaskpermission(user):
    referenced = ExternalGroup.objects.create(origin="Zammad", name="Guarded")
    _age(referenced, 200)
    UserTaskPermission.objects.create(
        user=user,
        allowed_external_group=referenced,
        access_level="FULL",
    )

    call_command("prune_external_groups", days=90)

    # Kept despite being stale — deleting it would CASCADE-delete the override.
    assert ExternalGroup.objects.filter(pk=referenced.pk).exists()
    assert UserTaskPermission.objects.filter(
        allowed_external_group=referenced
    ).exists()


def test_dry_run_deletes_nothing():
    stale = ExternalGroup.objects.create(origin="Zammad", name="Old")
    _age(stale, 200)

    call_command("prune_external_groups", days=90, dry_run=True)

    assert ExternalGroup.objects.filter(pk=stale.pk).exists()
