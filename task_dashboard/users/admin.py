from allauth.account.decorators import secure_admin_login
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.models import SocialToken
from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import ExternalGroup
from .models import ServiceConfiguration
from .models import Task
from .models import TaskPermission
from .models import User

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    # Force the `admin` sign in process to go through the `django-allauth` workflow:
    # https://docs.allauth.org/en/latest/common/admin.html#admin
    admin.autodiscover()
    admin.site.login = secure_admin_login(admin.site.login)  # type: ignore[method-assign]

try:
    admin.site.unregister(SocialApp)
    admin.site.unregister(SocialToken)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Personal info"), {"fields": ("name",)}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined")}),
    )
    list_display = ["email", "name", "is_superuser"]
    search_fields = ["name"]
    ordering = ["id"]
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "password1", "password2"),
            },
        ),
    )


class ServiceConfigurationForm(forms.ModelForm):
    class Meta:
        model = ServiceConfiguration
        fields = [
            "name",
            "service_type",
            "default_access_level",
            "api_url",
            "api_token",
            "api_username",
            "api_password",
            "is_active",
        ]
        widgets = {
            "api_token": forms.PasswordInput(render_value=True),
            "api_password": forms.PasswordInput(render_value=True),
        }


@admin.register(ServiceConfiguration)
class ServiceConfigurationAdmin(admin.ModelAdmin):
    form = ServiceConfigurationForm
    list_display = [
        "name",
        "service_type",
        "default_access_level",
        "api_url",
        "is_active",
    ]
    list_editable = ["is_active", "default_access_level"]
    list_filter = ["service_type", "is_active", "default_access_level"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "service_type",
                    "default_access_level",
                    "is_active",
                ),
            },
        ),
        (
            _("API Configuration"),
            {
                "fields": (
                    "api_url",
                    "api_token",
                    "api_username",
                    "api_password",
                ),
            },
        ),
    )


# 1. Allow managing permissions directly inside the Django Group page
class TaskPermissionInline(admin.TabularInline):
    model = TaskPermission
    extra = 1
    autocomplete_fields = ["allowed_external_group"]


# Unregister default Group admin and re-register with our Inline
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin):
    inlines = [TaskPermissionInline]


# 2. Manage Discovered Groups (Read Only mostly, as they are auto-created)
@admin.register(ExternalGroup)
class ExternalGroupAdmin(admin.ModelAdmin):
    list_display = ["origin", "name", "last_seen", "display_extra_data"]
    list_filter = ["origin"]
    search_fields = ["name", "origin", "extra_data"]
    ordering = ["origin", "name"]
    readonly_fields = ["last_seen"]

    @admin.display(description="Extra Data (Slug/ID)")
    def display_extra_data(self, obj):
        if not obj.extra_data:
            return "-"
        # Return a concise string representation
        return ", ".join(f"{k}: {v}" for k, v in obj.extra_data.items())


# 3. Direct Permission Management
@admin.register(TaskPermission)
class TaskPermissionAdmin(admin.ModelAdmin):
    list_display = ["django_group", "allowed_external_group"]
    list_filter = ["django_group", "allowed_external_group__origin"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "service", "external_id", "status", "priority", "owner"]
    list_filter = ["service", "status", "priority", "group"]
    search_fields = ["title", "external_id", "customer", "owner", "owner_email"]
    ordering = ["-updated_at"]
