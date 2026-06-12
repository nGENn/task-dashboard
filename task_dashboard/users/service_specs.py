"""
Single source of truth for per-service-type admin field configuration.

Adding a new external service? Add ONE entry to :data:`SERVICE_SPECS` below and
the admin form adapts automatically: the service shows up in the
``service_type`` dropdown, and only the credential/option fields you list become
visible when it is selected (via Unfold's ``conditional_fields`` / Alpine
``x-show``).

This registry only drives the **admin UI**. A new service still needs its own
client class under ``task_dashboard/services/`` (or ``kimai/``) and any
``service_type == "..."`` branches in the sync code.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Conditional (service-specific) config field names -----------------------
# ServiceConfiguration fields that only apply to *some* services. The
# always-present fields (name, service_type, default_access_level, is_active,
# api_url) are intentionally NOT listed here — they show for every service.
API_TOKEN = "api_token"  # noqa: S105 — field name, not a secret
API_USERNAME = "api_username"
API_PASSWORD = "api_password"  # noqa: S105 — field name, not a secret
KIMAI_CUSTOMER_NAME = "kimai_customer_name"

CONDITIONAL_FIELDS: tuple[str, ...] = (
    API_TOKEN,
    API_USERNAME,
    API_PASSWORD,
    KIMAI_CUSTOMER_NAME,
)


@dataclass(frozen=True)
class ServiceSpec:
    """Declares how one service type appears in the admin."""

    key: str
    """Stored ``service_type`` value."""

    label: str
    """Dropdown label. Keep as a plain str so ``choices`` stay migration-stable."""

    fields: frozenset[str]
    """Which :data:`CONDITIONAL_FIELDS` apply to (are shown for) this service."""


# Order here = order in the admin dropdown. Keep the existing order and labels so
# the model field's ``choices`` stay byte-identical (no spurious migration).
#
# Auth model per service:
#   token-based (Bearer/API key) -> API_TOKEN
#   HTTP Basic (Eramba only)     -> API_USERNAME + API_PASSWORD
# KIMAI_CUSTOMER_NAME overrides the Kimai customer for activities pushed *from*
# this service, so every source service has it — but not Kimai itself (it is the
# push target, overriding its own customer is meaningless).
SERVICE_SPECS: tuple[ServiceSpec, ...] = (
    ServiceSpec("zammad", "Zammad", frozenset({API_TOKEN, KIMAI_CUSTOMER_NAME})),
    ServiceSpec("gitlab", "GitLab", frozenset({API_TOKEN, KIMAI_CUSTOMER_NAME})),
    ServiceSpec("espocrm", "EspoCRM", frozenset({API_TOKEN, KIMAI_CUSTOMER_NAME})),
    ServiceSpec(
        "eramba",
        "Eramba",
        frozenset({API_USERNAME, API_PASSWORD, KIMAI_CUSTOMER_NAME}),
    ),
    ServiceSpec(
        "openproject", "OpenProject", frozenset({API_TOKEN, KIMAI_CUSTOMER_NAME})
    ),
    ServiceSpec("kimai", "Kimai", frozenset({API_TOKEN})),
)

# Derived ``choices`` for ServiceConfiguration.service_type.
SERVICE_TYPE_CHOICES: list[tuple[str, str]] = [(s.key, s.label) for s in SERVICE_SPECS]


def build_conditional_fields() -> dict[str, str]:
    """
    Map each conditional field to an Alpine.js boolean expression for Unfold's
    ``ModelAdmin.conditional_fields`` (which drives ``x-show`` on the field row).

    A field is shown only for the service types whose spec lists it, e.g.::

        {
            "api_username": "service_type == 'eramba'",
            "api_token": "service_type == 'zammad' || ... || service_type == 'kimai'",
        }
    """
    conditions: dict[str, str] = {}
    for field in CONDITIONAL_FIELDS:
        keys = [s.key for s in SERVICE_SPECS if field in s.fields]
        expr = " || ".join(f"service_type == '{k}'" for k in keys)
        # No service uses the field -> never show it (empty x-show is truthy).
        conditions[field] = expr or "false"
    return conditions
