import contextlib

from django.apps import AppConfig
from django.db import connection
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


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
