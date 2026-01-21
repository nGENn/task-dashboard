import pytest
from allauth.socialaccount.models import SocialAccount, SocialLogin
from django.contrib.auth.models import Group
from django.test import RequestFactory
from ticket_dashboard.users.adapters import SocialAccountAdapter
from ticket_dashboard.users.models import User

pytestmark = pytest.mark.django_db


def test_pre_social_login_keycloak_group_sync(rf: RequestFactory, db):
    """
    Test that Keycloak groups are synced to the Django user.
    """
    adapter = SocialAccountAdapter()
    request = rf.get("/")
    
    # Create a user
    user = User.objects.create(email="test@example.com")
    
    # Create a SocialAccount with extra_data containing groups
    social_account = SocialAccount(
        user=user,
        provider="keycloak",
        uid="12345",
        extra_data={"groups": ["admin", "editor"]}
    )
    
    social_login = SocialLogin(user=user, account=social_account)
    
    # Pre-existing group that should NOT be removed if we are just adding
    # OR it SHOULD be removed if we are doing strict sync.
    # The requirement says "Update the user's groups: Clear existing groups
    # (or smarter diffing) and set the user's groups to the list from
    # Keycloak."
    # So let's test strict sync.
    other_group = Group.objects.create(name="other")
    user.groups.add(other_group)
    
    # Run the method
    adapter.pre_social_login(request, social_login)
    
    # Verify groups were created and assigned
    assert Group.objects.filter(name="admin").exists()
    assert Group.objects.filter(name="editor").exists()

    user_groups = list(user.groups.values_list("name", flat=True))
    assert "admin" in user_groups
    assert "editor" in user_groups
    # Strict sync check: "other" should be removed
    assert "other" not in user_groups


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
        extra_data={"groups": ["ignored"]}
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
        extra_data={"policy": ["policy-admin", "policy-user"]}
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
        extra_data={"roles": ["role-admin"]}
    )
    social_login = SocialLogin(user=user, account=social_account)
    
    adapter.pre_social_login(request, social_login)
    
    user_groups = list(user.groups.values_list("name", flat=True))
    assert "role-admin" in user_groups
