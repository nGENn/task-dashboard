import pytest
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.models import SocialLogin
from django.contrib.auth.models import Group
from django.test import RequestFactory

from task_dashboard.users.adapters import _FALLBACK_GROUP
from task_dashboard.users.adapters import SocialAccountAdapter
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import User

pytestmark = pytest.mark.django_db


def _make_login(user, provider="keycloak", extra_data=None):
    account = SocialAccount(
        user=user,
        provider=provider,
        uid="test-uid",
        extra_data=extra_data or {},
    )
    return SocialLogin(user=user, account=account)


def test_keycloak_groups_synced(rf: RequestFactory):
    """Token groups are added; previously-synced groups no longer in token are removed;
    manually assigned groups are never touched."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="test@example.com")

    manual_group = Group.objects.create(name="manual-admin")
    user.groups.add(manual_group)

    old_sso_group = Group.objects.create(name="old-sso-group")
    user.groups.add(old_sso_group)
    user.sso_synced_groups = ["old-sso-group"]
    user.save()

    login = _make_login(user, extra_data={"groups": ["new-sso-group"]})
    adapter.pre_social_login(rf.get("/"), login)

    user.refresh_from_db()
    names = set(user.groups.values_list("name", flat=True))

    assert "new-sso-group" in names
    assert "manual-admin" in names  # untouched
    assert "old-sso-group" not in names  # removed because it was previously synced
    assert user.sso_synced_groups == ["new-sso-group"]


def test_non_keycloak_provider_skipped(rf: RequestFactory):
    """Providers other than keycloak / openid_connect do not trigger group sync."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="other@example.com")
    login = _make_login(user, provider="google", extra_data={"groups": ["ignored"]})

    adapter.pre_social_login(rf.get("/"), login)

    assert not Group.objects.filter(name="ignored").exists()
    assert user.groups.count() == 0


def test_no_groups_in_token_uses_configured_default(rf: RequestFactory):
    """When the token has no groups, the admin-configured fallback group is used."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="nogroups@example.com")

    setting = GlobalSetting.load()
    setting.sso_default_group = "configured-fallback"
    setting.save()

    login = _make_login(user, extra_data={})
    adapter.pre_social_login(rf.get("/"), login)

    user.refresh_from_db()
    names = set(user.groups.values_list("name", flat=True))
    assert "configured-fallback" in names
    assert user.sso_synced_groups == ["configured-fallback"]


def test_no_groups_in_token_no_default_uses_builtin_fallback(rf: RequestFactory):
    """When the token has no groups and no default is configured, 'sso-default-fallback'
    is created and assigned."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="fallback@example.com")

    setting = GlobalSetting.load()
    setting.sso_default_group = ""
    setting.save()

    login = _make_login(user, extra_data={})
    adapter.pre_social_login(rf.get("/"), login)

    user.refresh_from_db()
    names = set(user.groups.values_list("name", flat=True))
    assert _FALLBACK_GROUP in names
    assert Group.objects.filter(name=_FALLBACK_GROUP).exists()


def test_fallback_group_replaced_when_token_provides_groups(rf: RequestFactory):
    """A user previously in the fallback group gets moved to real SSO groups
    once Keycloak starts sending groups."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="upgrade@example.com")
    fallback = Group.objects.create(name=_FALLBACK_GROUP)
    user.groups.add(fallback)
    user.sso_synced_groups = [_FALLBACK_GROUP]
    user.save()

    login = _make_login(user, extra_data={"groups": ["real-group"]})
    adapter.pre_social_login(rf.get("/"), login)

    user.refresh_from_db()
    names = set(user.groups.values_list("name", flat=True))
    assert "real-group" in names
    assert _FALLBACK_GROUP not in names


def test_policy_field_used_when_groups_missing(rf: RequestFactory):
    """'policy' claim is used when 'groups' is absent from the token."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="policy@example.com")
    login = _make_login(user, extra_data={"policy": ["policy-admin", "policy-user"]})

    adapter.pre_social_login(rf.get("/"), login)

    names = set(user.groups.values_list("name", flat=True))
    assert "policy-admin" in names
    assert "policy-user" in names


def test_roles_field_used_as_last_fallback(rf: RequestFactory):
    """'roles' claim is used when neither 'groups' nor 'policy' is present."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="roles@example.com")
    login = _make_login(user, extra_data={"roles": ["role-admin"]})

    adapter.pre_social_login(rf.get("/"), login)

    names = set(user.groups.values_list("name", flat=True))
    assert "role-admin" in names


def test_ignored_groups_excluded(rf: RequestFactory):
    """Keycloak internal groups like 'offline_access' are never created in Django."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="ignored@example.com")
    login = _make_login(
        user,
        extra_data={"groups": ["real-group", "offline_access", "uma_authorization"]},
    )

    adapter.pre_social_login(rf.get("/"), login)

    names = set(user.groups.values_list("name", flat=True))
    assert "real-group" in names
    assert "offline_access" not in names
    assert "uma_authorization" not in names


def test_groups_in_userinfo_and_id_token_merged(rf: RequestFactory):
    """Groups nested inside 'userinfo' and 'id_token' sub-dicts are merged."""
    adapter = SocialAccountAdapter()
    user = User.objects.create(email="nested@example.com")
    login = _make_login(
        user,
        extra_data={
            "userinfo": {"groups": ["from-userinfo"]},
            "id_token": {"groups": ["from-id-token"]},
        },
    )

    adapter.pre_social_login(rf.get("/"), login)

    names = set(user.groups.values_list("name", flat=True))
    assert "from-userinfo" in names
    assert "from-id-token" in names
