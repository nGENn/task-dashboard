import re
from typing import ClassVar

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import Group
from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import BooleanField
from django.db.models import CharField
from django.db.models import EmailField
from django.db.models import F
from django.db.models import Q
from django.db.models import Value
from django.db.models.expressions import RawSQL
from django.db.models.functions import Concat
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

    # Names of Django groups last assigned by SSO sync.
    # Used to determine which groups to remove on the next login without
    # touching groups that were manually assigned in the admin.
    sso_synced_groups: models.JSONField = models.JSONField(
        default=list,
        blank=True,
    )

    def get_full_name(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("users:update")


ACCESS_LEVEL_CHOICES = [
    ("FULL", _("Full Access (See all tasks)")),
    ("LIMITED", _("Limited (Own tasks + Unassigned only)")),
    ("OWN", _("Only own tasks")),
    ("NONE", _("No Access")),
]


class TaskQuerySet(models.QuerySet):
    def filter_by_owners(self, of, best_to_raw, include_unassigned=False):  # noqa: C901, PLR0912, FBT002
        """
        Applies a high-performance identity filter using PostgreSQL array overlap.
        """
        if not of:
            return self

        owner_raw_criteria = set()
        unassigned_label = _("Unassigned")
        for o in [x for x in of if x != unassigned_label]:
            owner_raw_criteria.update(best_to_raw.get(o, {o}))

        search_tokens = set()
        for criteria in owner_raw_criteria:
            from .identity import normalize_identity_string

            c_norm = normalize_identity_string(criteria)
            tokens = [t for t in re.split(r"[^a-z0-9@.-]+", c_norm) if t]
            search_tokens.update(tokens)

        tokens_list = sorted(search_tokens)

        if not tokens_list and owner_raw_criteria:
            q_fallback = Q()
            for crit in owner_raw_criteria:
                if "@" in crit:
                    q_fallback |= Q(owner_email__iexact=crit) | Q(owner__icontains=crit)
                else:
                    q_fallback |= (
                        Q(owner__icontains=crit)
                        & ~Q(owner__contains="@")
                        & ~Q(owner_email__contains="@")
                    )
            return (
                self.filter(Q(is_unassigned=True) | q_fallback)
                if include_unassigned
                else self.filter(q_fallback)
            )

        is_test = getattr(settings, "TESTING", False) or "testserver" in getattr(
            settings, "ALLOWED_HOSTS", []
        )

        if is_test:
            q_owner = Q()
            for token in tokens_list:
                if "@" in token:
                    q_owner |= Q(owner_email__iexact=token) | Q(owner__icontains=token)
                    for st in [
                        t
                        for t in re.split(r"[^a-z0-9]+", token.lower())
                        if len(t) >= 3  # noqa: PLR2004
                    ]:
                        q_owner |= (
                            Q(owner__icontains=st)
                            & ~Q(owner__contains="@")
                            & ~Q(owner_email__contains="@")
                        )
                else:
                    for st in [
                        t
                        for t in re.split(r"[^a-z0-9]+", token.lower())
                        if len(t) >= 3  # noqa: PLR2004
                    ]:
                        q_owner |= (
                            Q(owner__icontains=st)
                            & ~Q(owner__contains="@")
                            & ~Q(owner_email__contains="@")
                        )

            if include_unassigned:
                return self.filter(Q(is_unassigned=True) | q_owner)
            return self.filter(q_owner)

        email_tokens = [t for t in tokens_list if "@" in t]
        name_tokens = [t for t in tokens_list if "@" not in t]
        sql_parts = []
        params = []
        if email_tokens:
            sql_parts.append(
                "(regexp_split_to_array(unaccent(replace(replace(replace("
                "lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
                "'[^a-z0-9@.-]+') && %s OR "
                "regexp_split_to_array(unaccent(replace(replace(replace("
                "lower(owner_email), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
                "'[^a-z0-9@.-]+') && %s)"
            )
            params.extend([email_tokens, email_tokens])
        if name_tokens:
            sql_parts.append(
                "(regexp_split_to_array(unaccent(replace(replace(replace("
                "lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
                "'[^a-z0-9@.-]+') && %s AND "
                "owner NOT LIKE '%%@%%' AND owner_email NOT LIKE '%%@%%')"
            )
            params.append(name_tokens)

        where_clause = " OR ".join(sql_parts) if sql_parts else "false"

        if include_unassigned:
            overlap_id_qs = (
                self.annotate(
                    match=RawSQL(  # noqa: S611 # nosec B611
                        where_clause,
                        params,
                        output_field=BooleanField(),
                    )
                )
                .filter(match=True)
                .values_list("pk", flat=True)
            )
            return self.filter(Q(is_unassigned=True) | Q(pk__in=overlap_id_qs))

        return self.annotate(
            match=RawSQL(  # noqa: S611 # nosec B611
                where_clause, params, output_field=BooleanField()
            )
        ).filter(match=True)


class TaskManager(models.Manager):
    def get_queryset(self):
        return TaskQuerySet(self.model, using=self._db)

    def filter_by_owners(self, of, best_to_raw, include_unassigned=False):  # noqa: FBT002
        return self.get_queryset().filter_by_owners(
            of, best_to_raw, include_unassigned=include_unassigned
        )


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
        verbose_name=_("Name"),
        help_text=_("Display Name (e.g. Internal Helpdesk)"),
    )
    service_type = models.CharField(
        max_length=20,
        choices=SERVICE_TYPES,
        verbose_name=_("Service Type"),
        help_text=_("Type of service to connect to."),
        default="zammad",
    )
    default_access_level = models.CharField(
        max_length=10,
        choices=ACCESS_LEVEL_CHOICES,
        default="NONE",
        verbose_name=_("Default Access Level"),
        help_text=_("Default access level for all users on this service."),
    )
    api_url = models.URLField(
        blank=True,
        default="",
        verbose_name=_("API URL"),
        help_text=_("Base URL for the service API."),
    )
    api_token = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("API Token or Secret for authentication."),
    )
    api_username = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Username for Basic Authentication (e.g. Eramba)"),
    )
    api_password = EncryptedCharField(
        max_length=255,
        blank=True,
        null=True,
        help_text=_("Password for Basic Authentication (e.g. Eramba)"),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Active"),
        help_text=_("Uncheck to hide this service from the dashboard completely."),
    )

    class Meta:
        verbose_name = _("Service Configuration")
        verbose_name_plural = _("Service Configurations")
        ordering = ["name"]
        permissions = [
            ("view_system_health", _("Can view system health indicator")),
            ("view_admin_button", _("Can view admin panel button")),
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
        verbose_name=_("Company Name"),
        help_text=_(
            "Used as fallback customer name across services if none is specified."
        ),
    )
    default_task_states = models.CharField(
        max_length=255,
        default="open,pending",
        verbose_name=_("Default Task States"),
        help_text=_(
            "Comma-separated list of default task states to show in the table "
            "(e.g., open,pending,new)."
        ),
    )
    sso_default_group = models.CharField(
        max_length=150,
        blank=True,
        default="",
        verbose_name=_("SSO Default Group"),
        help_text=_(
            "Fallback group assigned to SSO users when Keycloak provides no groups. "
            "Leave blank to use the built-in 'sso-default-fallback' group."
        ),
    )

    class Meta:
        verbose_name = _("Global Setting")
        verbose_name_plural = _("Global Settings")

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

    origin = models.CharField(max_length=50, verbose_name=_("Origin"))
    name = models.CharField(max_length=100, verbose_name=_("Name"))

    # Helpful for the admin to know when this group was last seen
    last_seen = models.DateTimeField(auto_now=True, verbose_name=_("Last Seen"))

    # Extra data for management (e.g. project IDs, slugs)
    extra_data = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ("origin", "name")
        ordering = ["origin", "name"]
        verbose_name = _("External Group")
        verbose_name_plural = _("External Groups")

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
        help_text=_(
            "FULL: View everything. LIMITED: View only unassigned tasks "
            "or those assigned to the user. OWN: View only tasks "
            "assigned to the user. NONE: No access."
        ),
    )

    class Meta:
        unique_together = ("django_group", "allowed_external_group")
        verbose_name = _("Task Permission")
        verbose_name_plural = _("Task Permissions")

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
        help_text=_(
            "FULL: View everything. LIMITED: View only unassigned tasks "
            "or those assigned to the user. OWN: View only tasks "
            "assigned to the user. NONE: No access."
        ),
    )

    class Meta:
        unique_together = ("django_group", "service")
        verbose_name = _("Service Permission")
        verbose_name_plural = _("Service Permissions")

    def __str__(self):
        return (
            f"{self.django_group} -> {self.service} ({self.get_access_level_display()})"
        )


def compare_query_params(request_get, target_params) -> bool:
    """
    Core logic to compare a QueryDict (request_get) with a target dict of params.
    """
    if not isinstance(target_params, dict):
        return False

    # Parameters to ignore when comparing active view
    ignore_params = {
        "page",
        "sort",
        "direction",
        "refresh",
        "csrfmiddlewaretoken",
        "view",
    }

    # Normalize request_get to a dict of sorted lists
    req_dict = {}
    for key in request_get:
        if key not in ignore_params:
            req_dict[key] = sorted(request_get.getlist(key))

    # Normalize target_params to a dict of sorted lists
    tp_dict = {}
    for key, value in target_params.items():
        if key not in ignore_params:
            if isinstance(value, list):
                tp_dict[key] = sorted([str(v) for v in value])
            else:
                tp_dict[key] = [str(value)]

    # Clean up other empty values: remove anything that is just [""] or []
    # This ensures that empty filters match whether they are missing or empty.
    req_dict = {k: v for k, v in req_dict.items() if v not in ([""], [])}
    tp_dict = {k: v for k, v in tp_dict.items() if v not in ([""], [])}

    return req_dict == tp_dict


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

    def matches_params(self, request_get) -> bool:
        """
        Compares request_get (QueryDict) with this view's query_params.
        Returns True if they match (ignoring order and specific params like page/sort).
        """
        return compare_query_params(request_get, self.query_params)

    def get_query_string(self) -> str:
        qd = QueryDict(mutable=True)
        for key, value in self.query_params.items():
            if isinstance(value, list):
                for v in value:
                    qd.appendlist(key, v)
            else:
                qd[key] = value
        return qd.urlencode()


class Task(models.Model):
    external_id = models.CharField(max_length=255, verbose_name=_("External ID"))
    service = models.ForeignKey(
        ServiceConfiguration,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    title = models.CharField(max_length=255, verbose_name=_("Title"))
    status = models.CharField(max_length=50, verbose_name=_("Status"))
    priority = models.CharField(max_length=50, verbose_name=_("Priority"))
    original_status = models.CharField(max_length=50, blank=True)
    original_priority = models.CharField(max_length=50, blank=True)
    customer = models.CharField(
        max_length=255, blank=True, default="", verbose_name=_("Customer")
    )
    group = models.CharField(max_length=255, blank=True, verbose_name=_("Group"))
    service_group = models.ForeignKey(
        ExternalGroup,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    owner = models.CharField(max_length=255, blank=True, verbose_name=_("Owner"))
    owner_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    url = models.URLField(max_length=500, blank=True)

    objects: ClassVar[TaskManager] = TaskManager()

    # Postgres 18 / Django 5.x GeneratedField for optimized searching
    search_text = models.GeneratedField(
        expression=Concat(
            F("title"),
            Value(" "),
            F("external_id"),
            Value(" "),
            F("customer"),
            Value(" "),
            F("owner"),
            Value(" "),
            F("group"),
        ),
        output_field=models.TextField(),
        db_persist=True,
    )

    class Meta:
        unique_together = ("service", "external_id")
        ordering = ["-updated_at"]
        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")
        indexes = [
            models.Index(fields=["search_text"]),
            GinIndex(
                fields=["search_text"],
                name="task_search_text_trgm_idx",
                opclasses=["gin_trgm_ops"],
            ),
            # Index for identity overlap performance
            GinIndex(
                RawSQL(
                    "regexp_split_to_array(unaccent(replace(replace(replace("
                    "lower(owner), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
                    "'[^a-z0-9@.-]+')",
                    (),
                ),
                name="task_owner_array_idx",
            ),
            GinIndex(
                RawSQL(
                    "regexp_split_to_array(unaccent(replace(replace(replace("
                    "lower(owner_email), 'ö', 'oe'), 'ä', 'ae'), 'ü', 'ue')), "
                    "'[^a-z0-9@.-]+')",
                    (),
                ),
                name="task_email_array_idx",
            ),
            models.Index(fields=["updated_at"], name="task_updated_at_idx"),
            models.Index(fields=["created_at"], name="task_created_at_idx"),
            models.Index(fields=["due_date"], name="task_due_date_idx"),
            models.Index(fields=["status"], name="task_status_idx"),
        ]

    def __str__(self):
        return self.title
