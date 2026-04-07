import pytest
from allauth.socialaccount.models import SocialAccount
from allauth.socialaccount.models import SocialLogin
from django.contrib.auth.models import Group
from django.test import RequestFactory

from task_dashboard.users.adapters import SocialAccountAdapter
from task_dashboard.users.models import SSOGroup
from task_dashboard.users.models import User

pytestmark = pytest.mark.django_db


def test_pre_social_login_keycloak_group_sync(rf: RequestFactory, db):
    """
    Test that Keycloak groups are synced to the Django user,
    but manually assigned groups are NOT removed.
    """

    adapter = SocialAccountAdapter()
    request = rf.get("/")

    # Create a user
    user = User.objects.create(email="test@example.com")

    # 1. Create a manual group and assign it to the user
    manual_group = Group.objects.create(name="manual-admin")
    user.groups.add(manual_group)

    # 2. Create an old SSO group that is no longer in the payload
    old_sso_group = Group.objects.create(name="old-sso-group")
    SSOGroup.objects.create(group=old_sso_group)
    user.groups.add(old_sso_group)

    # 3. Prepare SocialLogin with NEW groups
    social_account = SocialAccount(
        user=user,
        provider="keycloak",
        uid="12345",
        extra_data={"groups": ["new-sso-group"]},
    )
    social_login = SocialLogin(user=user, account=social_account)

    # Run the method
    adapter.pre_social_login(request, social_login)

    # Verify groups
    user_groups = list(user.groups.values_list("name", flat=True))

    # New SSO group should be added
    assert "new-sso-group" in user_groups
    assert SSOGroup.objects.filter(group__name="new-sso-group").exists()

    # Manual group should REMAINS
    assert "manual-admin" in user_groups

    # Old SSO group should be REMOVED
    assert "old-sso-group" not in user_groups


def test_pre_social_login_non_keycloak_no_sync(rf: RequestFactory, db):
    """
    Test that non-keycloak logins do not trigger group sync (unless specified).
    """
    adapter = SocialAccountAdapter()
    request = rf.get("/")

    user = User.objects.create(email="other@example.com")
    social_account = SocialAccount(
        user=user,
        provider="google",
        uid="67890",
        extra_data={"groups": ["ignored"]},
    )
    social_login = SocialLogin(user=user, account=social_account)

    adapter.pre_social_login(request, social_login)

    assert not Group.objects.filter(name="ignored").exists()
    assert user.groups.count() == 0


def test_pre_social_login_fallback_policy_sync(rf: RequestFactory, db):
    """
    Test that 'policy' field is used if 'groups' is missing.
    """
    adapter = SocialAccountAdapter()
    request = rf.get("/")
    user = User.objects.create(email="policy@example.com")
    social_account = SocialAccount(
        user=user,
        provider="keycloak",
        uid="54321",
        extra_data={"policy": ["policy-admin", "policy-user"]},
    )
    social_login = SocialLogin(user=user, account=social_account)

    adapter.pre_social_login(request, social_login)

    user_groups = list(user.groups.values_list("name", flat=True))
    assert "policy-admin" in user_groups
    assert "policy-user" in user_groups


def test_pre_social_login_fallback_roles_sync(rf: RequestFactory, db):
    """
    Test that 'roles' field is used if 'groups' and 'policy' are missing.
    """
    adapter = SocialAccountAdapter()
    request = rf.get("/")
    user = User.objects.create(email="roles@example.com")
    social_account = SocialAccount(
        user=user,
        provider="keycloak",
        uid="98765",
        extra_data={"roles": ["role-admin"]},
    )
    social_login = SocialLogin(user=user, account=social_account)

    adapter.pre_social_login(request, social_login)

    user_groups = list(user.groups.values_list("name", flat=True))
    assert "role-admin" in user_groups
