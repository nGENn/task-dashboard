from typing import ClassVar

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import CharField
from django.db.models import EmailField
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

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
    name = models.CharField(
        max_length=50, unique=True, help_text="Service Name (e.g. Zammad, GitLab)"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Uncheck to hide this service from the dashboard completely.",
    )

    def __str__(self):
        return f"{self.name} ({'Active' if self.is_active else 'Disabled'})"

    class Meta:
        verbose_name = "Service Configuration"
        verbose_name_plural = "Service Configurations"
        ordering = ["name"]
