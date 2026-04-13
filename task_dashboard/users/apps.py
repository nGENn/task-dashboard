import contextlib

from django.apps import AppConfig
from django.db import connection
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _


def setup_periodic_tasks(sender, **kwargs):
    """
    Sets up periodic tasks for the users app using Django Q.
    """
    # Moving to top-level causes AppRegistryNotReady
    from django_q.models import Schedule

    # Check if django_q_schedule table exists before trying to use it
    if "django_q_schedule" not in connection.introspection.table_names():
        return

    func = "task_dashboard.users.tasks.fetch_all_tasks_task"
    Schedule.objects.get_or_create(
        func=func,
        defaults={
            "name": "Fetch All Tasks",
            "schedule_type": Schedule.MINUTES,
            "minutes": 5,
            "repeats": -1,
        },
    )


class UsersConfig(AppConfig):
    name = "task_dashboard.users"
    verbose_name = _("Users")

    def ready(self):
        # Moving to top-level causes AppRegistryNotReady
        from .tasks import fetch_all_tasks_task  # noqa: F401

        with contextlib.suppress(ImportError):
            import task_dashboard.users.signals  # noqa: F401

        post_migrate.connect(setup_periodic_tasks, sender=self)
