from allauth.account.decorators import secure_admin_login
from allauth.socialaccount.models import SocialApp
from allauth.socialaccount.models import SocialToken
from django.conf import settings
from django import forms
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import ExternalGroup
from .models import ServiceConfiguration
from .models import TicketPermission
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
            "api_url",
            "api_token",
            "is_active",
        ]
        widgets = {
            "api_token": forms.PasswordInput(render_value=True),
        }


@admin.register(ServiceConfiguration)
class ServiceConfigurationAdmin(admin.ModelAdmin):
    form = ServiceConfigurationForm
    list_display = ["name", "service_type", "api_url", "is_active"]
    list_editable = ["is_active"]
    list_filter = ["service_type", "is_active"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "service_type",
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
                ),
            },
        ),
    )


# 1. Allow managing permissions directly inside the Django Group page
class TicketPermissionInline(admin.TabularInline):
    model = TicketPermission
    extra = 1
    autocomplete_fields = ["allowed_external_group"]


# Unregister default Group admin and re-register with our Inline
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin):
    inlines = [TicketPermissionInline]


# 2. Manage Discovered Groups (Read Only mostly, as they are auto-created)
@admin.register(ExternalGroup)
class ExternalGroupAdmin(admin.ModelAdmin):
    list_display = ["origin", "name", "last_seen"]
    list_filter = ["origin"]
    search_fields = ["name", "origin"]
    ordering = ["origin", "name"]


# 3. Direct Permission Management
@admin.register(TicketPermission)
class TicketPermissionAdmin(admin.ModelAdmin):
    list_display = ["django_group", "allowed_external_group"]
    list_filter = ["django_group", "allowed_external_group__origin"]
