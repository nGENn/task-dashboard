from __future__ import annotations

import logging
import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth.models import Group

from task_dashboard.users.models import SSOGroup
from task_dashboard.users.models import User

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def save_user(self, request, user, form, *, commit=True):
        """
        This is called when a user is registered (Local OR Social).
        We override it to assign a default group.
        """
        # 1. Standard Save logic
        user = super().save_user(request, user, form, commit)

        # 2. Assign Default Group
        # Change "Users" to whatever group name you want as default
        default_group_name = "Users"

        if commit:
            try:
                # get_or_create ensures we don't crash if the group is missing
                group, _ = Group.objects.get_or_create(name=default_group_name)
                user.groups.add(group)
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "Error assigning default group: %s",
                    e,
                )

        return user


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def populate_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        data: dict[str, typing.Any],
    ) -> User:
        """
        Populates user information from social provider info.
        """
        user = super().populate_user(request, sociallogin, data)
        if not user.name:
            if name := data.get("name"):
                user.name = name
            elif first_name := data.get("first_name"):
                user.name = first_name
                if last_name := data.get("last_name"):
                    user.name += f" {last_name}"
        return user

    def pre_social_login(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> None:
        """
        Triggered before a social login is completed.
        Used to sync Keycloak groups to Django groups.
        """
        super().pre_social_login(request, sociallogin)

        # Sync groups if user already exists
        if sociallogin.user.pk:
            self._sync_groups(sociallogin, sociallogin.user)

    def save_user(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
        form: typing.Any = None,
    ) -> User:
        """
        Called when a social user is created for the first time.
        """
        user = super().save_user(request, sociallogin, form)
        self._sync_groups(sociallogin, user)
        return user

    def _extract_groups(self, data: typing.Any) -> set[str]:
        """
        Helper to extract group names from a dictionary.
        Looks for 'groups', 'policy', and 'roles' keys.
        """
        if not isinstance(data, dict):
            return set()

        extracted: set[str] = set()
        for key in ["groups", "policy", "roles"]:
            val = data.get(key)
            if isinstance(val, list):
                extracted.update(str(item) for item in val)
            elif isinstance(val, str):
                extracted.add(val)
        return extracted

    def _sync_groups(self, sociallogin: SocialLogin, user: User) -> None:
        """
        Syncs Keycloak groups from social account data to Django groups.
        """

        # 1. Check provider
        provider = sociallogin.account.provider
        if provider not in ["keycloak", "openid_connect"]:
            return

        # 2. Get groups from extra_data (including nested locations)
        extra_data = sociallogin.account.extra_data

        # We look into top-level, userinfo, and id_token as requested
        all_groups = self._extract_groups(extra_data)
        all_groups.update(self._extract_groups(extra_data.get("userinfo")))
        all_groups.update(self._extract_groups(extra_data.get("id_token")))

        # 3. Filter out technical scopes and handle ignored groups
        ignored_groups = {"offline_access", "uma_authorization"}
        groups_names = [g for g in all_groups if g not in ignored_groups]

        # 4. Sync groups
        # Ensure groups exist and collect them
        django_groups = []
        for name in groups_names:
            group, _ = Group.objects.get_or_create(name=name)
            # Mark it as an SSO group
            SSOGroup.objects.get_or_create(group=group)
            django_groups.append(group)

        # 5. Assign groups to user
        # Only manage SSO groups; leave manual groups alone.
        if user.pk:
            # Current SSO groups the user belongs to
            sso_group_ids = SSOGroup.objects.values_list("group_id", flat=True)
            current_user_sso_groups = set(user.groups.filter(id__in=sso_group_ids))

            # New SSO groups from this login
            new_sso_groups = set(django_groups)

            # Diff
            to_remove = current_user_sso_groups - new_sso_groups
            to_add = new_sso_groups - current_user_sso_groups

            if to_remove:
                user.groups.remove(*to_remove)
            if to_add:
                user.groups.add(*to_add)
