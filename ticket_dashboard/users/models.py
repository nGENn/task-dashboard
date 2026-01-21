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
    Default custom user model for Ticket Dashboard.
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
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this service from the dashboard completely.",
    )

    class Meta:
        verbose_name = "Service Configuration"
        verbose_name_plural = "Service Configurations"
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.get_service_type_display()})"


class ExternalGroup(models.Model):
    """
    Auto-discovered groups from your services.
    Example: Origin="Zammad", Name="Support"
    """

    origin = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    # Helpful for the admin to know when this group was last seen
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("origin", "name")
        ordering = ["origin", "name"]

    def __str__(self):
        return f"{self.origin} - {self.name}"


class TicketPermission(models.Model):
    """
    Rules connecting Django Groups to External Groups.
    """

    # 1. Define the Choices
    ACCESS_CHOICES = [
        ("FULL", "Full Access (See all tickets)"),
        ("LIMITED", "Limited (Own tickets + Unassigned only)"),
        ("OWN_ONLY", "Only own tickets"),
    ]

    django_group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="ticket_permissions",
    )
    allowed_external_group = models.ForeignKey(ExternalGroup, on_delete=models.CASCADE)

    # 2. Add the new field
    access_level = models.CharField(
        max_length=10,
        choices=ACCESS_CHOICES,
        default="FULL",
        help_text=(
            "FULL: View everything. LIMITED: View only unassigned tickets "
            "or those assigned to the user. OWN_ONLY: View only tickets "
            "assigned to the user."
        ),
    )

    class Meta:
        unique_together = ("django_group", "allowed_external_group")
        verbose_name = "Ticket Permission"
        verbose_name_plural = "Ticket Permissions"

    def __str__(self):
        return (
            f"{self.django_group} -> {self.allowed_external_group} "
            f"({self.get_access_level_display()})"
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
