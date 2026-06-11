import pytest

from task_dashboard.users.models import EmailConfiguration
from task_dashboard.users.models import GlobalSetting
from task_dashboard.users.models import SSOConfiguration
from task_dashboard.users.models import User


def test_user_get_absolute_url(user: User):
    assert user.get_absolute_url() == "/users/~update/"


pytestmark_db = pytest.mark.django_db


@pytest.mark.django_db
class TestSingletons:
    """The admin-configurable singletons all pin pk=1 and load() is idempotent."""

    def test_global_setting_load_is_singleton(self):
        a = GlobalSetting.load()
        b = GlobalSetting.load()
        assert a.pk == 1
        assert b.pk == 1
        assert GlobalSetting.objects.count() == 1

    def test_global_setting_save_forces_pk_1(self):
        obj = GlobalSetting(company_name="Acme")
        obj.save()
        assert obj.pk == 1
        assert GlobalSetting.objects.count() == 1

    def test_email_configuration_load_is_singleton(self):
        assert EmailConfiguration.load().pk == 1
        assert EmailConfiguration.objects.count() == 1

    def test_email_get_connection_none_when_disabled(self):
        cfg = EmailConfiguration.load()
        cfg.enabled = False
        cfg.host = "smtp.example.com"
        assert cfg.get_connection() is None

    def test_email_get_connection_none_without_host(self):
        cfg = EmailConfiguration.load()
        cfg.enabled = True
        cfg.host = ""
        assert cfg.get_connection() is None

    def test_email_get_connection_built_when_enabled(self):
        cfg = EmailConfiguration.load()
        cfg.enabled = True
        cfg.host = "smtp.example.com"
        cfg.port = 2525
        conn = cfg.get_connection()
        assert conn is not None
        assert conn.host == "smtp.example.com"
        assert conn.port == cfg.port


@pytest.mark.django_db
class TestSSOConfiguration:
    def test_load_is_singleton(self):
        assert SSOConfiguration.load().pk == 1
        assert SSOConfiguration.objects.count() == 1

    def test_is_configured_false_when_disabled_or_incomplete(self):
        cfg = SSOConfiguration.load()
        cfg.enabled = False
        cfg.server_url = "https://kc.example.com/realms/r"
        cfg.client_id = "cid"
        cfg.client_secret = "secret"  # noqa: S105
        assert cfg.is_configured is False

        cfg.enabled = True
        cfg.client_secret = ""
        assert cfg.is_configured is False

    def test_is_configured_true_when_complete(self):
        cfg = SSOConfiguration.load()
        cfg.enabled = True
        cfg.server_url = "https://kc.example.com/realms/r"
        cfg.client_id = "cid"
        cfg.client_secret = "secret"  # noqa: S105
        assert cfg.is_configured is True

    def test_encrypted_secret_roundtrips(self):
        cfg = SSOConfiguration.load()
        cfg.client_secret = "top-secret-value"  # noqa: S105
        cfg.save()
        reloaded = SSOConfiguration.objects.get(pk=1)
        assert reloaded.client_secret == "top-secret-value"  # noqa: S105

    def test_to_social_app_mirrors_settings(self):
        cfg = SSOConfiguration.load()
        cfg.provider_name = "Corp SSO"
        cfg.server_url = "https://kc.example.com/realms/r"
        cfg.client_id = "cid"
        cfg.client_secret = "secret"  # noqa: S105
        app = cfg.to_social_app()
        assert app.provider == "openid_connect"
        assert app.provider_id == "keycloak"
        assert app.name == "Corp SSO"
        assert app.client_id == "cid"
        assert app.secret == "secret"  # noqa: S105
        assert app.settings == {"server_url": "https://kc.example.com/realms/r"}
