from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin

from task_dashboard.users.admin_site import admin_site

from .models import KimaiSettings


@admin.register(KimaiSettings, site=admin_site)
class KimaiSettingsAdmin(ModelAdmin):
    fieldsets = (
        (
            _("Reminder"),
            {
                "fields": (
                    "reminder_enabled",
                    "grace_period_days",
                    "holiday_country",
                    "exempt_emails",
                ),
            },
        ),
        (
            _("Activity Sync"),
            {"fields": ("sync_enabled",)},
        ),
        (
            _("Team Sync"),
            {"fields": ("team_sync_enabled",)},
        ),
    )

    def has_add_permission(self, request):
        return not KimaiSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
