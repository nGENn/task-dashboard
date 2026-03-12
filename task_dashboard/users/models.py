from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group
from django.db import models
from django.db.models import CharField
from django.db.models import EmailField
from django.http import QueryDict
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from .fields import EncryptedCharField
from .managers import UserManager


class User(AbstractUser):
    """
    Default custom user model for Task Dashboard.
    If adding fields that need to be filled at user signup,
    check forms.SignupForm and forms.SocialSignupForms accordingly.
    """

    # First and last name do not cover name patterns around the globe
    name = CharField(_("Name of User"), blank=True, max_length=255)
    first_name = None  # type: ignore[assignment]
    last_name = None  # type: ignore[assignment]
    email = EmailField(_("email address"), unique=True)
    username = None  # type: ignore[assignment]

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects: ClassVar[UserManager] = UserManager()

    def get_absolute_url(self) -> str:
        """Get URL for user's detail view.

        Returns:
            str: URL for user detail.

        """
        return reverse("users:detail", kwargs={"pk": self.id})


ACCESS_LEVEL_CHOICES = [
    ("FULL", "Full Access (See all tasks)"),
    ("LIMITED", "Limited (Own tasks + Unassigned only)"),
    ("OWN", "Only own tasks"),
    ("NONE", "No Access"),
]


class ServiceConfiguration(models.Model):
    SERVICE_TYPES = [
        ("zammad", "Zammad"),
        ("gitlab", "GitLab"),
        ("espocrm", "EspoCRM"),
        ("eramba", "Eramba"),
        ("openproject", "OpenProject"),
    ]

    name = models.CharField(
        max_length=50,
        unique=True,
        help_text="Display Name (e.g. Internal Helpdesk)",
    )
    service_type = models.CharField(
        max_length=20,
        choices=SERVICE_TYPES,
        help_text="Type of service to connect to.",
        default="zammad",
    )
    default_access_level = models.CharField(
        max_length=10,
        choices=ACCESS_LEVEL_CHOICES,
        default="NONE",
        help_text="Default access level for all users on this service.",
    )
    api_url = models.URLField(
        blank=True,
        default="",
        help_text="Base URL for the service API.",
    )
    api_token = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="API Token or Secret for authentication.",
    )
    api_username = models.CharField(
        max_length=255,
        blank=True,
        help_text="Username for Basic Authentication (e.g. Eramba)",
    )
    api_password = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Password for Basic Authentication (e.g. Eramba)",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this service from the dashboard completely.",
    )

    class Meta:
        verbose_name = "Service Configuration"
        verbose_name_plural = "Service Configurations"
        ordering = ["name"]
        permissions = [
            ("view_system_health", "Can view system health indicator"),
            ("view_admin_button", "Can view admin panel button"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_service_type_display()})"


class GlobalSetting(models.Model):
    """
    Singleton model for global dashboard settings.
    """

    company_name = models.CharField(
        max_length=100,
        default="Internal",
        help_text=(
            "Used as fallback customer name across services if none is specified."
        ),
    )

    class Meta:
        verbose_name = "Global Setting"
        verbose_name_plural = "Global Settings"

    def __str__(self):
        return "Global Setting"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class ExternalGroup(models.Model):
    """
    Auto-discovered groups from your services.
    Example: Origin="Zammad", Name="Support"
    """

    origin = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    # Helpful for the admin to know when this group was last seen
    last_seen = models.DateTimeField(auto_now=True)

    # Extra data for management (e.g. project IDs, slugs)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("origin", "name")
        ordering = ["origin", "name"]

    def __str__(self):
        return f"{self.origin} - {self.name}"


class TaskPermission(models.Model):
    """
    Rules connecting Django Groups to External Groups.
    """

    django_group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="task_permissions",
    )
    allowed_external_group = models.ForeignKey(ExternalGroup, on_delete=models.CASCADE)

    # 2. Add the new field
    access_level = models.CharField(
        max_length=10,
        choices=ACCESS_LEVEL_CHOICES,
        default="NONE",
        help_text=(
            "FULL: View everything. LIMITED: View only unassigned tasks "
            "or those assigned to the user. OWN: View only tasks "
            "assigned to the user. NONE: No access."
        ),
    )

    class Meta:
        unique_together = ("django_group", "allowed_external_group")
        verbose_name = "Task Permission"
        verbose_name_plural = "Task Permissions"

    def __str__(self):
        return (
            f"{self.django_group} -> {self.allowed_external_group} "
            f"({self.get_access_level_display()})"
        )


class ServicePermission(models.Model):
    """
    Rules connecting Django Groups to Services.
    """

    django_group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="service_permissions",
    )
    service = models.ForeignKey(
        ServiceConfiguration,
        on_delete=models.CASCADE,
    )
    access_level = models.CharField(
        max_length=10,
        choices=ACCESS_LEVEL_CHOICES,
        default="NONE",
        help_text=(
            "FULL: View everything. LIMITED: View only unassigned tasks "
            "or those assigned to the user. OWN: View only tasks "
            "assigned to the user. NONE: No access."
        ),
    )

    class Meta:
        unique_together = ("django_group", "service")
        verbose_name = "Service Permission"
        verbose_name_plural = "Service Permissions"

    def __str__(self):
        return (
            f"{self.django_group} -> {self.service} ({self.get_access_level_display()})"
        )


class SavedView(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_views",
    )
    name = models.CharField(max_length=100)
    query_params = models.JSONField(
        default=dict,
        help_text="JSON dictionary of query parameters",
    )
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("user", "name")

    def __str__(self):
        return f"{self.user.email} - {self.name}"

    def get_query_string(self) -> str:
        """Returns the query parameters as a URL-encoded string."""
        qd = QueryDict(mutable=True)
        for key, value in self.query_params.items():
            if isinstance(value, list):
                for v in value:
                    qd.appendlist(key, v)
            else:
                qd[key] = value
        return qd.urlencode()


class Task(models.Model):
    external_id = models.CharField(max_length=255)
    service = models.ForeignKey(
        ServiceConfiguration,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=255)
    status = models.CharField(max_length=50)
    priority = models.CharField(max_length=50)
    customer = models.CharField(max_length=255, blank=True, default="")
    group = models.CharField(max_length=255, blank=True)
    owner = models.CharField(max_length=255, blank=True)
    owner_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)

    class Meta:
        unique_together = ("service", "external_id")
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title
