from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from task_dashboard.users.admin_site import SingletonModelAdmin
from task_dashboard.users.admin_site import admin_site

from .models import KimaiSettings


@admin.register(KimaiSettings, site=admin_site)
class KimaiSettingsAdmin(SingletonModelAdmin, ModelAdmin):
    autocomplete_fields = ["exempt_owners"]
    fieldsets = (
        (
            _("Reminder"),
            {
                "fields": (
                    "reminder_enabled",
                    "reminder_interval_minutes",
                    "grace_period_days",
                    "holiday_country",
                    "exempt_owners",
                ),
            },
        ),
        (
            _("Reminder Emails"),
            {
                "fields": (
                    "reminder_email_enabled",
                    "reminder_email_hour",
                    "reminder_email_subject",
                    "reminder_email_body",
                ),
            },
        ),
        (
            _("Activity Sync"),
            {
                "fields": (
                    "sync_enabled",
                    "sync_interval_minutes",
                    "deactivation_grace_days",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return not KimaiSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
