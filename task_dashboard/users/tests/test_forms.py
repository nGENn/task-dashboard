"""Module for all Form Tests."""

import pytest
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from task_dashboard.users.forms import GlobalSettingForm
from task_dashboard.users.forms import UserAdminCreationForm
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import User

pytestmark = pytest.mark.django_db


class TestGlobalSettingForm:
    def test_sso_default_group_shows_existing_groups(self):
        Group.objects.create(name="alpha")
        Group.objects.create(name="beta")
        instance = GlobalSetting.load()
        form = GlobalSettingForm(instance=instance)
        sso_choices = form.fields["sso_default_group"].choices  # type: ignore[attr-defined]
        choice_values = [c[0] for c in sso_choices]
        assert "" in choice_values
        assert "alpha" in choice_values
        assert "beta" in choice_values

    def test_empty_selection_is_valid_and_saves_blank(self):
        instance = GlobalSetting.load()
        form = GlobalSettingForm(
            instance=instance,
            data={
                "company_name": "Test",
                "default_task_states": "open",
                "default_task_states_list": ["open"],
                "sso_default_group": "",
            },
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.sso_default_group == ""

    def test_existing_group_selection_saves_name(self):
        Group.objects.create(name="my-sso-group")
        instance = GlobalSetting.load()
        form = GlobalSettingForm(
            instance=instance,
            data={
                "company_name": "Test",
                "default_task_states": "open",
                "default_task_states_list": ["open"],
                "sso_default_group": "my-sso-group",
            },
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.sso_default_group == "my-sso-group"

    def test_deleted_group_still_shown_with_warning(self):
        instance = GlobalSetting.load()
        instance.sso_default_group = "vanished-group"
        instance.save()
        form = GlobalSettingForm(instance=instance)
        sso_choices = form.fields["sso_default_group"].choices  # type: ignore[attr-defined]
        choice_values = [c[0] for c in sso_choices]
        labels = {c[0]: c[1] for c in sso_choices}
        assert "vanished-group" in choice_values
        assert "⚠" in labels["vanished-group"]


class TestUserAdminCreationForm:
    """
    Test class for all tests related to the UserAdminCreationForm
    """

    def test_username_validation_error_msg(self, user: User):
        """
        Tests UserAdminCreation Form's unique validator functions correctly by testing:
            1) A new user with an existing username cannot be added.
            2) Only 1 error is raised by the UserCreation Form
            3) The desired error message is raised
        """

        # The user already exists,
        # hence cannot be created.
        form = UserAdminCreationForm(
            {
                "email": user.email,
                "password1": user.password,
                "password2": user.password,
            },
        )

        assert not form.is_valid()
        assert len(form.errors) == 1
        assert "email" in form.errors
        assert form.errors["email"][0] == _("This email has already been taken.")
