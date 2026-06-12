import contextlib
from datetime import timedelta

from django.apps import AppConfig
from django.db import connection
from django.db.models.signals import post_migrate
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

DEFAULT_REMINDER_EMAIL_HOUR = 7


def next_run_at_hour(hour: int):
    """Return the next datetime at the given local hour (today or tomorrow)."""
    now = timezone.localtime()
    candidate = now.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _setup_schedules(sender, **kwargs):
    from django_q.models import Schedule

    if "django_q_schedule" not in connection.introspection.table_names():
        return

    schedules = [
        (
            "task_dashboard.kimai.tasks.sync_kimai_activities",
            "Kimai: Sync Activities",
            Schedule.MINUTES,
            {"minutes": 15},
        ),
        (
            "task_dashboard.kimai.tasks.refresh_kimai_user_cache",
            "Kimai: Refresh User Cache",
            Schedule.HOURLY,
            {},
        ),
        (
            "task_dashboard.kimai.tasks.run_reminder_evaluation",
            "Kimai: Reminder Evaluation",
            Schedule.MINUTES,
            {"minutes": 60},
        ),
        (
            "task_dashboard.kimai.tasks.send_kimai_reminder_emails",
            "Kimai: Reminder Emails (daily)",
            Schedule.DAILY,
            {"next_run": next_run_at_hour(DEFAULT_REMINDER_EMAIL_HOUR)},
        ),
    ]

    for func, name, schedule_type, extras in schedules:
        defaults = {"name": name, "schedule_type": schedule_type, "repeats": -1}
        defaults.update(extras)
        Schedule.objects.get_or_create(func=func, defaults=defaults)


class KimaiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "task_dashboard.kimai"
    verbose_name = _("Kimai Integration")

    def ready(self):
        with contextlib.suppress(Exception):
            post_migrate.connect(_setup_schedules, sender=self)
