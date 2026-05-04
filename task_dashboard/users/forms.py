from allauth.account.forms import LoginForm
from allauth.account.forms import SignupForm
from allauth.socialaccount.forms import SignupForm as SocialSignupForm
from django import forms
from django.contrib.auth import forms as admin_forms
from django.contrib.auth.models import Group
from django.forms import EmailField
from django.utils.translation import gettext_lazy as _

from .models import GlobalSetting
from .models import User


class GlobalSettingForm(forms.ModelForm):
    default_task_states_list = forms.MultipleChoiceField(
        choices=[
            ("open", _("Open")),
            ("pending", _("Pending")),
            ("closed", _("Closed")),
        ],
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label=_("Default Task States"),
        help_text=_("Select the default task states to show in the table."),
    )
    sso_default_group = forms.ChoiceField(
        required=False,
        label=_("SSO Default Group"),
        help_text=_(
            "Group assigned to SSO users when Keycloak provides no groups. "
            "Leave blank to use the built-in 'sso-default-fallback' group."
        ),
    )

    class Meta:
        model = GlobalSetting
        fields = [
            "company_name",
            "default_task_states",
            "sso_default_group",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.default_task_states:
            self.fields["default_task_states_list"].initial = [
                s.strip()
                for s in self.instance.default_task_states.split(",")
                if s.strip()
            ]
        choices: list[tuple[str, str]] = [
            ("", str(_("— sso-default-fallback (built-in) —")))
        ]
        choices += [(g.name, g.name) for g in Group.objects.order_by("name")]
        # If the stored value no longer exists as a group, keep it visible
        current = getattr(self.instance, "sso_default_group", "") or ""
        if current and not any(c[0] == current for c in choices):
            choices.append((current, f"{current} ⚠ (group deleted)"))
        sso_field = self.fields["sso_default_group"]
        assert isinstance(sso_field, forms.ChoiceField)
        sso_field.choices = choices

    def save(self, commit=True):  # noqa: FBT002
        instance = super().save(commit=False)
        selected_states = self.cleaned_data.get("default_task_states_list", [])
        instance.default_task_states = ",".join(selected_states)
        if commit:
            instance.save()
        return instance


class UserAdminChangeForm(admin_forms.UserChangeForm):
    class Meta(admin_forms.UserChangeForm.Meta):  # type: ignore[name-defined]
        model = User
        field_classes = {"email": EmailField}


class UserAdminCreationForm(admin_forms.AdminUserCreationForm):
    """
    Form for User Creation in the Admin Area.
    To change user signup, see UserSignupForm and UserSocialSignupForm.
    """

    class Meta(admin_forms.UserCreationForm.Meta):  # type: ignore[name-defined]
        model = User
        fields = ("email",)
        field_classes = {"email": EmailField}
        error_messages = {
            "email": {"unique": _("This email has already been taken.")},
        }


class UserSignupForm(SignupForm):
    """
    Form that will be rendered on a user sign up section/screen.
    Default fields will be added automatically.
    Check UserSocialSignupForm for accounts created from social.
    """


class UserSocialSignupForm(SocialSignupForm):
    """
    Renders the form when user has signed up using social accounts.
    Default fields will be added automatically.
    See UserSignupForm otherwise.
    """


class UserLoginForm(LoginForm):
    """
    Custom Login Form to add autocomplete attributes for Bitwarden/Password Managers.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Email field
        self.fields["login"].widget.attrs.update(
            {
                "autocomplete": "username email",
                "autofocus": "autofocus",
            }
        )
        # Password field
        self.fields["password"].widget.attrs.update(
            {
                "autocomplete": "current-password",
            }
        )
