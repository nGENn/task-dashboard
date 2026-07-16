from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from task_dashboard.users.models import ExternalGroup

DEFAULT_STALE_DAYS = 90


class Command(BaseCommand):
    help = (
        "Delete ExternalGroups not seen for N days (default 90). Groups still "
        "referenced by a TaskPermission or UserTaskPermission are always kept, "
        "so pruning never silently removes an RBAC rule (the FK cascades)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=DEFAULT_STALE_DAYS,
            help=f"Stale threshold in days (default {DEFAULT_STALE_DAYS}).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be deleted without deleting.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        # Keep any group referenced by a group- or user-level permission —
        # deleting one would CASCADE-delete the RBAC rule.
        stale = (
            ExternalGroup.objects.filter(last_seen__lt=cutoff)
            .exclude(taskpermission__isnull=False)
            .exclude(usertaskpermission__isnull=False)
        )
        count = stale.count()

        if dry_run:
            self.stdout.write(
                f"[dry-run] {count} ExternalGroup(s) older than {days}d "
                f"and unreferenced would be deleted."
            )
            for g in stale.order_by("origin", "name")[:50]:
                self.stdout.write(
                    f"  - {g.origin} / {g.name} (last seen {g.last_seen})"
                )
            return

        with transaction.atomic():
            deleted, _ = stale.delete()
        self.stdout.write(
            self.style.SUCCESS(
                f"Pruned {deleted} stale ExternalGroup(s) "
                f"(older than {days}d, unreferenced by any permission)."
            )
        )
