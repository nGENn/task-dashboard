import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


class KimaiSettings(models.Model):
    grace_period_days = models.IntegerField(
        default=3,
        verbose_name=_("Grace Period (days)"),
        help_text=_("Business days behind before reminder triggers."),
    )
    holiday_country = models.CharField(
        max_length=5,
        default="DE",
        verbose_name=_("Holiday Country Code"),
        help_text=_("ISO 3166-1 alpha-2 country code for public holidays (e.g. DE)."),
    )
    exempt_owners = models.ManyToManyField(
        "users.TaskOwner",
        blank=True,
        verbose_name=_("Exempt Emails"),
        help_text=_(
            "Emails (incl. discovered, not-yet-registered) excluded from "
            "reminders and reminder emails."
        ),
    )
    sync_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Activity Sync Enabled"),
        help_text=_("Sync service tasks to Kimai activities."),
    )
    sync_interval_minutes = models.PositiveIntegerField(
        default=15,
        verbose_name=_("Sync Interval (minutes)"),
        help_text=_(
            "How often to sync activities to Kimai. Takes effect after saving."
        ),
    )
    reminder_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Reminder Enabled"),
        help_text=_("Show reminder banner when user is behind on time tracking."),
    )
    reminder_interval_minutes = models.PositiveIntegerField(
        default=60,
        verbose_name=_("Reminder Interval (minutes)"),
        help_text=_(
            "How often to evaluate reminder status. Takes effect after saving."
        ),
    )
    reminder_email_enabled = models.BooleanField(
        default=False,
        verbose_name=_("Reminder Emails Enabled"),
        help_text=_("Send a daily email to owners who are behind on time tracking."),
    )
    reminder_email_hour = models.PositiveIntegerField(
        default=7,
        verbose_name=_("Reminder Email Hour"),
        help_text=_(
            "Hour of day (0-23, server time) to send the daily reminder email."
        ),
    )

    class Meta:
        verbose_name = _("Kimai Settings")
        verbose_name_plural = _("Kimai Settings")

    def __str__(self):
        return "Kimai Settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        try:
            from django_q.models import Schedule

            Schedule.objects.filter(
                func="task_dashboard.kimai.tasks.sync_kimai_activities"
            ).update(minutes=self.sync_interval_minutes, schedule_type=Schedule.MINUTES)
            Schedule.objects.filter(
                func="task_dashboard.kimai.tasks.run_reminder_evaluation"
            ).update(
                minutes=self.reminder_interval_minutes, schedule_type=Schedule.MINUTES
            )
            from .apps import next_run_at_hour

            Schedule.objects.filter(
                func="task_dashboard.kimai.tasks.send_kimai_reminder_emails"
            ).update(
                next_run=next_run_at_hour(self.reminder_email_hour),
                schedule_type=Schedule.DAILY,
            )
        except ImportError:
            pass
        except Exception:
            logger.exception("Failed to update Django-Q schedules for KimaiSettings")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def get_exempt_email_set(self) -> set[str]:
        return {o.email.strip().lower() for o in self.exempt_owners.all() if o.email}
