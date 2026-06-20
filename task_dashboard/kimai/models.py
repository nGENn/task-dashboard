import logging

from django.db import models
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

DEFAULT_REMINDER_EMAIL_SUBJECT = (
    "{% if never_booked %}Reminder: please start tracking your time in Kimai"
    "{% else %}Reminder: you are {{ days_behind }} working days behind in Kimai"
    "{% endif %}"
)

DEFAULT_REMINDER_EMAIL_BODY = """Hi {{ name }},

{% if never_booked %}We have no time-tracking entries for you in Kimai yet. \
Please start booking your hours.{% else %}You are currently {{ days_behind }} \
working days behind on your time tracking in Kimai \
(grace period: {{ grace_period }} days).{% endif %}

{% if kimai_url %}Open Kimai: {{ kimai_url }}{% endif %}

Thanks,
Task Dashboard
"""


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
    deactivation_grace_days = models.PositiveIntegerField(
        default=14,
        verbose_name=_("Deactivation Grace Period (days)"),
        help_text=_(
            "When a task closes, its Kimai activity is left active for this many "
            "days before being deactivated (hidden, never deleted) — so people "
            "can still book a little time after the task is closed. Set to 0 to "
            "deactivate immediately on close."
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
    reminder_email_subject = models.CharField(
        max_length=255,
        default=DEFAULT_REMINDER_EMAIL_SUBJECT,
        verbose_name=_("Reminder Email Subject"),
        help_text=_(
            "Subject line for the reminder email. Placeholders: {{ name }}, "
            "{{ days_behind }}, {{ grace_period }}, {{ kimai_url }}, "
            "{{ never_booked }}."
        ),
    )
    reminder_email_body = models.TextField(
        default=DEFAULT_REMINDER_EMAIL_BODY,
        verbose_name=_("Reminder Email Message"),
        help_text=_(
            "Body of the reminder email (plain text). Placeholders: "
            "{{ name }}, {{ days_behind }}, {{ grace_period }}, "
            "{{ kimai_url }}, {{ never_booked }}. Django template tags such "
            "as {% if never_booked %}…{% endif %} are supported."
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


class KimaiActivityFlag(models.Model):
    """A closed/removed task whose Kimai activity is pending deactivation.

    Kimai activities are not deactivated the moment their source task closes —
    people may still need to book a little time against them. Instead the task
    is flagged here, the activity stays active for
    ``KimaiSettings.deactivation_grace_days``, then the sync hides it
    (``visible=False``; we never delete in Kimai). If the task reopens before
    the grace period elapses the flag is removed and the activity is unhidden.

    Persisted (not cached) because the grace period spans weeks while every
    Kimai cache TTL is <= 24h.
    """

    config = models.ForeignKey(
        "users.ServiceConfiguration",
        on_delete=models.CASCADE,
        related_name="kimai_activity_flags",
    )
    external_id = models.CharField(max_length=255)
    flagged_at = models.DateField(
        help_text=_("Date the source task was first seen closed/removed."),
    )
    deactivated = models.BooleanField(
        default=False,
        help_text=_("Set once the grace period elapsed and the activity was hidden."),
    )

    class Meta:
        verbose_name = _("Kimai Activity Flag")
        verbose_name_plural = _("Kimai Activity Flags")
        constraints = [
            models.UniqueConstraint(
                fields=["config", "external_id"],
                name="unique_kimai_activity_flag_per_task",
            ),
        ]

    def __str__(self):
        return f"{self.config_id}:{self.external_id} (flagged {self.flagged_at})"
