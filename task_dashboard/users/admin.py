from allauth.account.decorators import secure_admin_login
from django import forms
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import admin as auth_admin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from django_q.admin import FailAdmin
from django_q.admin import ScheduleAdmin
from django_q.admin import TaskAdmin as QTaskAdmin
from django_q.models import Failure
from django_q.models import Schedule
from django_q.models import Success
from unfold.admin import ModelAdmin
from unfold.admin import TabularInline

from .admin_site import SingletonModelAdmin
from .admin_site import admin_site
from .forms import GlobalSettingForm
from .forms import UserAdminChangeForm
from .forms import UserAdminCreationForm
from .models import EmailConfiguration
from .models import ExternalGroup
from .models import GlobalSetting
from .models import ServiceConfiguration
from .models import ServicePermission
from .models import SSOConfiguration
from .models import Task
from .models import TaskOwner
from .models import TaskPermission
from .models import User
from .models import UserServicePermission
from .models import UserTaskPermission
from .service_specs import build_conditional_fields

if settings.DJANGO_ADMIN_FORCE_ALLAUTH:
    admin.autodiscover()
    admin_site.login = secure_admin_login(admin_site.login)


class UserTaskPermissionInline(TabularInline):
    model = UserTaskPermission
    extra = 1
    autocomplete_fields = ["allowed_external_group"]
    verbose_name_plural = _("Task permission overrides (take precedence over groups)")


class UserServicePermissionInline(TabularInline):
    model = UserServicePermission
    extra = 1
    verbose_name_plural = _(
        "Service permission overrides (take precedence over groups)"
    )


@admin.register(User, site=admin_site)
class UserAdmin(ModelAdmin, auth_admin.UserAdmin):
    form = UserAdminChangeForm
    add_form = UserAdminCreationForm
    inlines = [UserTaskPermissionInline, UserServicePermissionInline]

    def get_inline_instances(self, request, obj=None):
        # Overrides only make sense for an existing user — hide on the add page.
        if obj is None:
            return []
        return super().get_inline_instances(request, obj)

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
    search_fields = ["name", "email"]
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
            "kimai_customer_name",
        ]
        widgets = {
            "api_token": forms.PasswordInput(render_value=True),
            "api_password": forms.PasswordInput(render_value=True),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Help text spells out which service(s) each credential field is for.
        # The fields themselves are also shown/hidden per service_type via the
        # admin's conditional_fields, but the text keeps it clear once visible.
        self.fields["api_url"].help_text = _("Base URL for the service API (HTTPS).")
        self.fields["api_token"].help_text = _(
            "API token / bearer secret. Used by Zammad, GitLab, EspoCRM, "
            "OpenProject and Kimai."
        )
        self.fields["api_username"].help_text = _(
            "Username for HTTP Basic authentication. Eramba only."
        )
        self.fields["api_password"].help_text = _(
            "Password for HTTP Basic authentication. Eramba only."
        )


@admin.register(ServiceConfiguration, site=admin_site)
class ServiceConfigurationAdmin(ModelAdmin):
    form = ServiceConfigurationForm
    # Drives Unfold's per-field x-show: each credential/option field is only
    # shown for the service types declared in service_specs.SERVICE_SPECS.
    conditional_fields = build_conditional_fields()
    list_display = [
        "name",
        "service_type",
        "default_access_level",
        "api_url",
        "is_active",
    ]
    list_editable = ["is_active", "default_access_level"]
    list_filter = ["service_type", "is_active", "default_access_level"]
    search_fields = ["name"]
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
            _("Connection"),
            {
                # api_url is always shown, so this section never fully empties;
                # the remaining fields appear only for the relevant service type.
                "fields": (
                    "api_url",
                    "api_token",
                    "api_username",
                    "api_password",
                    "kimai_customer_name",
                ),
                "description": _(
                    "Only the fields relevant to the selected service type are shown."
                ),
            },
        ),
    )


class TaskPermissionInline(TabularInline):
    model = TaskPermission
    extra = 1
    autocomplete_fields = ["allowed_external_group"]


class ServicePermissionInline(TabularInline):
    model = ServicePermission
    extra = 1


@admin.register(Group, site=admin_site)
class GroupAdmin(ModelAdmin, BaseGroupAdmin):
    inlines = [TaskPermissionInline, ServicePermissionInline]


@admin.register(TaskPermission, site=admin_site)
class TaskPermissionAdmin(ModelAdmin):
    list_display = ["django_group", "allowed_external_group", "access_level"]
    list_filter = ["access_level", "django_group"]
    search_fields = ["django_group__name", "allowed_external_group__name"]
    autocomplete_fields = ["allowed_external_group"]


@admin.register(ServicePermission, site=admin_site)
class ServicePermissionAdmin(ModelAdmin):
    list_display = ["django_group", "service", "access_level"]
    list_filter = ["access_level", "django_group", "service"]
    search_fields = ["django_group__name", "service__name"]


@admin.register(UserTaskPermission, site=admin_site)
class UserTaskPermissionAdmin(ModelAdmin):
    list_display = ["user", "allowed_external_group", "access_level"]
    list_filter = ["access_level"]
    search_fields = ["user__email", "user__name", "allowed_external_group__name"]
    autocomplete_fields = ["user", "allowed_external_group"]


@admin.register(UserServicePermission, site=admin_site)
class UserServicePermissionAdmin(ModelAdmin):
    list_display = ["user", "service", "access_level"]
    list_filter = ["access_level", "service"]
    search_fields = ["user__email", "user__name", "service__name"]
    autocomplete_fields = ["user"]


@admin.register(ExternalGroup, site=admin_site)
class ExternalGroupAdmin(ModelAdmin):
    list_display = ["origin", "name", "last_seen", "display_extra_data"]
    list_filter = ["origin"]
    search_fields = ["name", "origin", "extra_data"]
    ordering = ["origin", "name"]
    readonly_fields = ["last_seen"]

    @admin.display(description=_("Extra Data (Slug/ID)"))
    def display_extra_data(self, obj):
        if not obj.extra_data:
            return "-"
        return ", ".join(f"{k}: {v}" for k, v in obj.extra_data.items())


@admin.register(Task, site=admin_site)
class TaskAdmin(ModelAdmin):
    list_display = [
        "title",
        "service",
        "external_id",
        "status",
        "group",
        "service_group",
        "owner",
    ]
    list_filter = ["service", "status", "priority", "group", "service_group"]
    search_fields = ["title", "external_id", "customer", "owner", "owner_email"]
    ordering = ["-updated_at"]


@admin.register(TaskOwner, site=admin_site)
class TaskOwnerAdmin(ModelAdmin):
    list_display = ["email", "name", "user", "is_discovered", "last_seen"]
    list_filter = [("user", admin.EmptyFieldListFilter)]
    search_fields = ["email", "name"]
    ordering = ["name", "email"]
    readonly_fields = ["first_seen", "last_seen", "kimai_user_id"]
    autocomplete_fields = ["user"]
    actions = ["promote_to_user"]

    @admin.display(boolean=True, description=_("Discovered"))
    def is_discovered(self, obj):
        return obj.is_discovered

    @admin.action(description=_("Promote selected owners to users"))
    def promote_to_user(self, request, queryset):
        promoted = 0
        for owner in queryset.filter(user__isnull=True):
            owner.promote()
            promoted += 1
        self.message_user(
            request,
            _("%(n)d owner(s) promoted to users.") % {"n": promoted},
        )


class EmailConfigurationForm(forms.ModelForm):
    class Meta:
        model = EmailConfiguration
        fields = "__all__"  # noqa: DJ007
        widgets = {
            "password": forms.PasswordInput(render_value=True),
        }


@admin.register(EmailConfiguration, site=admin_site)
class EmailConfigurationAdmin(SingletonModelAdmin, ModelAdmin):
    form = EmailConfigurationForm
    fieldsets = (
        (None, {"fields": ("enabled", "default_from_email")}),
        (
            _("SMTP Server"),
            {
                "fields": (
                    "host",
                    "port",
                    "username",
                    "password",
                    "use_tls",
                    "use_ssl",
                    "timeout",
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return not EmailConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


class SSOConfigurationForm(forms.ModelForm):
    class Meta:
        model = SSOConfiguration
        fields = "__all__"  # noqa: DJ007
        widgets = {
            "client_secret": forms.PasswordInput(render_value=True),
        }


@admin.register(SSOConfiguration, site=admin_site)
class SSOConfigurationAdmin(SingletonModelAdmin, ModelAdmin):
    form = SSOConfigurationForm
    fieldsets = (
        (None, {"fields": ("enabled", "provider_name")}),
        (
            _("OIDC Provider"),
            {
                "fields": ("server_url", "client_id", "client_secret"),
                "description": _(
                    "When enabled, these settings take precedence over the "
                    "KEYCLOAK_* environment variables. The redirect URI to "
                    "register at the provider stays "
                    "/accounts/oidc/keycloak/login/callback/."
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return not SSOConfiguration.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GlobalSetting, site=admin_site)
class GlobalSettingAdmin(SingletonModelAdmin, ModelAdmin):
    form = GlobalSettingForm
    list_display = ["company_name", "sso_default_group", "default_task_states"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "company_name",
                    "sso_default_group",
                    "default_task_states_list",
                )
            },
        ),
        (
            _("Scheduling"),
            {"fields": ("task_fetch_interval_minutes",)},
        ),
        (
            _("Kimai"),
            {"fields": ("kimai_customer_country", "kimai_customer_timezone")},
        ),
    )

    def has_add_permission(self, request):
        if GlobalSetting.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


# ---------------------------------------------------------------------------
# Django-Q2 models — registered on custom admin_site so they appear in the
# Unfold admin instead of the default Django admin.
# ---------------------------------------------------------------------------


@admin.register(Schedule, site=admin_site)
class UnfoldScheduleAdmin(ModelAdmin, ScheduleAdmin):
    pass


@admin.register(Success, site=admin_site)
class UnfoldSuccessAdmin(ModelAdmin, QTaskAdmin):
    pass


@admin.register(Failure, site=admin_site)
class UnfoldFailureAdmin(ModelAdmin, FailAdmin):
    pass
