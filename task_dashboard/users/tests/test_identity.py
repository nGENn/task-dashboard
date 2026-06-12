"""Tests for owner-identity canonicalisation (identity.py)."""

import pytest
from django.utils import timezone

from task_dashboard.users.identity import build_email_canonical_map
from task_dashboard.users.identity import canonical_owner_for_label
from task_dashboard.users.identity import compute_global_bridging
from task_dashboard.users.models import ServiceConfiguration
from task_dashboard.users.models import Task
from task_dashboard.users.models import User


@pytest.mark.django_db
class TestBuildEmailCanonicalMap:
    """build_email_canonical_map collapses one person's address variants.

    Mirrors the dashboard's display-time identity bridging so the Kimai sync
    provisions one user/team per person instead of one per address variant.
    """

    def test_address_variants_collapse_to_shortest(self):
        """The reported bug: m.handsche@ and handsche@ are one person.

        With no linked Django user, the shortest valid email wins.
        """
        result = build_email_canonical_map(
            ["m.handsche@ngenn.net", "handsche@ngenn.net"]
        )
        assert result["m.handsche@ngenn.net"] == "handsche@ngenn.net"
        assert result["handsche@ngenn.net"] == "handsche@ngenn.net"

    def test_order_independent(self):
        """Same canonical regardless of input order (pool is sorted first)."""
        a = build_email_canonical_map(["handsche@ngenn.net", "m.handsche@ngenn.net"])
        b = build_email_canonical_map(["m.handsche@ngenn.net", "handsche@ngenn.net"])
        assert a == b

    def test_distinct_people_do_not_merge(self):
        """Unrelated emails each map to themselves."""
        result = build_email_canonical_map(
            ["alice@ngenn.net", "bob@ngenn.net", "handsche@ngenn.net"]
        )
        assert result["alice@ngenn.net"] == "alice@ngenn.net"
        assert result["bob@ngenn.net"] == "bob@ngenn.net"
        assert result["handsche@ngenn.net"] == "handsche@ngenn.net"

    def test_keys_and_values_lowercased(self):
        """Input case/whitespace is normalised on both sides."""
        result = build_email_canonical_map(
            ["  M.Handsche@NGENN.net ", "Handsche@ngenn.NET"]
        )
        assert result["m.handsche@ngenn.net"] == "handsche@ngenn.net"
        assert result["handsche@ngenn.net"] == "handsche@ngenn.net"

    def test_linked_django_user_email_wins(self):
        """A label matching a real User's email is preferred as canonical.

        Documents the tier-1 preference: if the longer variant is the actual
        Django user, that address — not merely the shortest — becomes canonical.
        """
        User.objects.create_user(
            email="m.handsche@ngenn.net",
            password="x",  # noqa: S106
        )
        result = build_email_canonical_map(
            ["m.handsche@ngenn.net", "handsche@ngenn.net"]
        )
        assert result["m.handsche@ngenn.net"] == "m.handsche@ngenn.net"
        assert result["handsche@ngenn.net"] == "m.handsche@ngenn.net"

    def test_empty_and_non_email_ignored(self):
        """Blank/comma-junk and non-email tokens are dropped from the pool."""
        result = build_email_canonical_map(["", "  ", "not-an-email", None])
        assert result == {}


@pytest.mark.django_db
class TestComputeGlobalBridging:
    """Global clustering over all task owners — single source of truth shared
    by the dashboard and the Kimai sync."""

    def _task(self, ext, service, **kw):
        return Task.objects.create(
            external_id=ext,
            title=ext,
            status="open",
            service=service,
            updated_at=timezone.now(),
            **kw,
        )

    def test_email_variants_dedup_across_services(self):
        """The original bug, end-to-end: variants from two different services
        collapse to one canonical email (a per-service pool could not)."""
        s1 = ServiceConfiguration.objects.create(
            name="Zammad", service_type="zammad", is_active=True
        )
        s2 = ServiceConfiguration.objects.create(
            name="GitLab", service_type="gitlab", is_active=True
        )
        self._task("Z1", s1, owner_email="m.handsche@ngenn.net", owner="Max Handsche")
        self._task("G1", s2, owner_email="handsche@ngenn.net", owner="")

        gb = compute_global_bridging()
        assert gb.email_canonical["m.handsche@ngenn.net"] == "handsche@ngenn.net"
        assert gb.email_canonical["handsche@ngenn.net"] == "handsche@ngenn.net"
        # Both raw labels live under the one canonical group.
        assert "m.handsche@ngenn.net" in gb.best_to_raw["handsche@ngenn.net"]

    def test_name_only_owner_bridges_to_seeded_user_email(self):
        """A name-only owner (e.g. a gitlab username, no email) adopts a known
        user's email as canonical via user seeding."""
        User.objects.create_user(
            email="last@example.com",
            password="x",  # noqa: S106
            name="First Last",
        )
        s = ServiceConfiguration.objects.create(
            name="GitLab", service_type="gitlab", is_active=True
        )
        self._task("G1", s, owner="flast", owner_email="")

        gb = compute_global_bridging()
        assert (
            canonical_owner_for_label("flast", gb.token_to_canonical)
            == "last@example.com"
        )

    def test_cross_domain_emails_not_merged(self):
        """Same local-part, different domains = different people."""
        s = ServiceConfiguration.objects.create(
            name="Zammad", service_type="zammad", is_active=True
        )
        self._task("Z1", s, owner_email="shared@domain-a.com", owner="")
        self._task("Z2", s, owner_email="shared@domain-b.com", owner="")

        gb = compute_global_bridging()
        assert gb.email_canonical["shared@domain-a.com"] == "shared@domain-a.com"
        assert gb.email_canonical["shared@domain-b.com"] == "shared@domain-b.com"
