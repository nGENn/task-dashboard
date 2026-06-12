import contextlib
from html import unescape
from http import HTTPStatus
from importlib import reload

import pytest
from django.contrib.admin import sites as admin_sites
from django.contrib.auth.models import AnonymousUser
from django.urls import reverse
from pytest_django.asserts import assertRedirects

from task_dashboard.users.admin_site import admin_site
from task_dashboard.users.models import User
from task_dashboard.users.service_specs import build_conditional_fields


class TestUserAdmin:
    def test_changelist(self, admin_client):
        url = reverse("admin:users_user_changelist")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

    def test_search(self, admin_client):
        url = reverse("admin:users_user_changelist")
        response = admin_client.get(url, data={"q": "test"})
        assert response.status_code == HTTPStatus.OK

    def test_add(self, admin_client):
        url = reverse("admin:users_user_add")
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

        response = admin_client.post(
            url,
            data={
                "email": "new-admin@example.com",
                "password1": "My_R@ndom-P@ssw0rd",
                "password2": "My_R@ndom-P@ssw0rd",
            },
        )
        assert response.status_code == HTTPStatus.FOUND
        assert User.objects.filter(email="new-admin@example.com").exists()

    def test_view_user(self, admin_client):
        user = User.objects.get(email="admin@example.com")
        url = reverse("admin:users_user_change", kwargs={"object_id": user.pk})
        response = admin_client.get(url)
        assert response.status_code == HTTPStatus.OK

    @pytest.fixture
    def _force_allauth(self, settings):
        settings.DJANGO_ADMIN_FORCE_ALLAUTH = True
        # Reload the admin module to apply the setting change
        import task_dashboard.users.admin as users_admin

        with contextlib.suppress(admin_sites.AlreadyRegistered):  # type: ignore[attr-defined]
            reload(users_admin)

    @pytest.mark.django_db
    @pytest.mark.usefixtures("_force_allauth")
    def test_allauth_login(self, rf, settings):
        request = rf.get("/fake-url")
        request.user = AnonymousUser()
        response = admin_site.login(request)

        # The `admin` login view should redirect to the `allauth` login view
        target_url = reverse(settings.LOGIN_URL) + "?next=" + request.path
        assertRedirects(response, target_url, fetch_redirect_response=False)


def _expr(*service_keys: str) -> str:
    """The Alpine x-show expression that shows a field for the given services."""
    return " || ".join(f"service_type == '{k}'" for k in service_keys)


class TestServiceConfigurationConditionalFields:
    """The per-service field visibility registry + its rendered wiring."""

    def test_username_password_are_eramba_only(self):
        cf = build_conditional_fields()
        assert cf["api_username"] == _expr("eramba")
        assert cf["api_password"] == _expr("eramba")

    def test_token_shown_for_all_except_eramba(self):
        cf = build_conditional_fields()
        assert cf["api_token"] == _expr(
            "zammad", "gitlab", "espocrm", "openproject", "kimai"
        )
        assert "'eramba'" not in cf["api_token"]

    def test_kimai_customer_override_hidden_for_kimai(self):
        cf = build_conditional_fields()
        assert cf["kimai_customer_name"] == _expr(
            "zammad", "gitlab", "espocrm", "eramba", "openproject"
        )
        assert "'kimai'" not in cf["kimai_customer_name"]

    @pytest.mark.django_db
    def test_add_form_emits_conditional_wiring(self, admin_client):
        """The add page renders Alpine x-show conditions + a reactive select."""
        response = admin_client.get(reverse("admin:users_serviceconfiguration_add"))
        assert response.status_code == HTTPStatus.OK
        # unescape so HTML entities (&#x27; etc.) match the source expressions.
        html = unescape(response.content.decode())
        # service_type becomes reactive Alpine state...
        assert 'x-model.fill="service_type"' in html
        # ...and the credential fields gate on it.
        assert "x-show=\"service_type == 'eramba'\"" in html


class TestSingletonAdminRedirect:
    @pytest.mark.django_db
    @pytest.mark.parametrize(
        "changelist",
        [
            "admin:users_globalsetting_changelist",
            "admin:users_emailconfiguration_changelist",
            "admin:kimai_kimaisettings_changelist",
        ],
    )
    def test_changelist_redirects_to_change_page(self, admin_client, changelist):
        response = admin_client.get(reverse(changelist))
        assert response.status_code == HTTPStatus.FOUND
        expected = reverse(changelist.replace("_changelist", "_change"), args=[1])
        assert response.url == expected
