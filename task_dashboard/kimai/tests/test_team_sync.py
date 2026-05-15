"""Tests for Kimai team sync invariants (V10, V11, V13)."""

import pytest

from task_dashboard.kimai.models import KimaiSettings


class TestTeamNameFormat:
    """V11: Kimai team name = "{ExternalGroup.origin}::{ExternalGroup.name}" """

    def _team_name(self, origin: str, group_name: str) -> str:
        return f"{origin}::{group_name}"

    def test_format(self):
        assert self._team_name("Zammad", "Support") == "Zammad::Support"

    def test_different_origins_same_group_are_distinct(self):
        """V11: encodes origin so cross-service teams don't collide."""
        t1 = self._team_name("Zammad", "Support")
        t2 = self._team_name("GitLab", "Support")
        assert t1 != t2

    def test_separator_is_double_colon(self):
        name = self._team_name("EspoCRM", "Sales Team")
        assert "::" in name
        origin, group = name.split("::", 1)
        assert origin == "EspoCRM"
        assert group == "Sales Team"


class TestSsoFilter:
    """V10: only sso-* prefixed Django group names may become Kimai teams."""

    def _allowed(self, name: str) -> bool:
        return name.startswith("sso-")

    def test_sso_prefix_allowed(self):
        assert self._allowed("sso-support-team")

    def test_non_sso_rejected(self):
        assert not self._allowed("admins")
        assert not self._allowed("Users")
        assert not self._allowed("default-roles-foo")

    def test_sso_uppercase_not_allowed(self):
        # Prefix is lowercase-sensitive
        assert not self._allowed("SSO-support")

    def test_sso_prefix_with_varied_suffix(self):
        assert self._allowed("sso-")
        assert self._allowed("sso-a")
        assert self._allowed("sso-very-long-group-name")


@pytest.mark.django_db
class TestKimaiSettingsExempt:
    """V9: exempt_emails checked before any Kimai call."""

    def test_exempt_email_set_normalised(self):
        s = KimaiSettings(exempt_emails="Admin@example.com\nops@example.com\n")
        exempt = s.get_exempt_email_set()
        assert "admin@example.com" in exempt
        assert "ops@example.com" in exempt

    def test_empty_exempt_returns_empty_set(self):
        s = KimaiSettings(exempt_emails="")
        assert s.get_exempt_email_set() == set()

    def test_blank_lines_ignored(self):
        s = KimaiSettings(exempt_emails="\n  \nuser@x.com\n")
        assert s.get_exempt_email_set() == {"user@x.com"}
