from __future__ import annotations

import logging
import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth.models import Group

from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import User

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

_SSO_PROVIDERS = frozenset({"keycloak", "openid_connect"})
_IGNORED_GROUPS = frozenset({"offline_access", "uma_authorization"})
_IGNORED_PREFIXES = ("default-roles-",)
_FALLBACK_GROUP = "sso-default-fallback"


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
        """Extract group names from a token payload dict.

        Checks 'groups', 'policy', and 'roles' keys in that order.
        """
        if not isinstance(data, dict):
            return set()

        extracted: set[str] = set()
        for key in ("groups", "policy", "roles"):
            val = data.get(key)
            if isinstance(val, list):
                extracted.update(str(item) for item in val)
            elif isinstance(val, str):
                extracted.add(val)
        return extracted

    def _sync_groups(self, sociallogin: SocialLogin, user: User) -> None:
        """Sync Keycloak groups to Django groups on every SSO login.

        Groups that were previously assigned by this method (tracked in
        user.sso_synced_groups) are removed if they no longer appear in
        the token. Groups assigned manually in the admin are never touched.

        SECURITY: To prevent privilege escalation, only groups that ALREADY EXIST
        in Django OR start with 'sso-' are allowed to be synced.
        """
        if sociallogin.account.provider not in _SSO_PROVIDERS:
            return

        extra_data = sociallogin.account.extra_data
        raw: set[str] = self._extract_groups(extra_data)
        raw.update(self._extract_groups(extra_data.get("userinfo")))
        raw.update(self._extract_groups(extra_data.get("id_token")))
        token_groups = {
            g
            for g in raw
            if g not in _IGNORED_GROUPS and not g.startswith(_IGNORED_PREFIXES)
        }

        if token_groups:
            target_names = token_groups
        else:
            setting = GlobalSetting.load()
            target_names = {setting.sso_default_group.strip() or _FALLBACK_GROUP}

        # SECURITY FILTER: Strictly only allow groups with 'sso-' prefix
        allowed_target_names = {
            name for name in target_names if name.startswith("sso-")
        }

        # Ensure all allowed target groups exist in Django
        target_groups: list[Group] = []
        for name in allowed_target_names:
            group, _ = Group.objects.get_or_create(name=name)
            target_groups.append(group)

        if not user.pk:
            user.groups.set(target_groups)
            return

        prev_synced: set[str] = set(user.sso_synced_groups or [])
        current_ids: set[int] = set(user.groups.values_list("id", flat=True))

        to_remove = Group.objects.filter(name__in=prev_synced - allowed_target_names)
        to_add = [g for g in target_groups if g.pk not in current_ids]

        if to_remove:
            user.groups.remove(*to_remove)
        if to_add:
            user.groups.add(*to_add)

        user.sso_synced_groups = sorted(allowed_target_names)
        user.save(update_fields=["sso_synced_groups"])
