from __future__ import annotations

import typing

from allauth.account.adapter import DefaultAccountAdapter
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings
from django.contrib.auth.models import Group  # <--- Added Import

if typing.TYPE_CHECKING:
    from allauth.socialaccount.models import SocialLogin
    from django.http import HttpRequest

    from ticket_dashboard.users.models import User


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", True)

    def save_user(self, request, user, form, commit=True):
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
            except Exception as e:
                # Log error but let the user sign in
                print(f"Error assigning default group: {e}")

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
