import pytest

from task_dashboard.users.fields import EncryptedCharField


@pytest.mark.django_db
def test_encrypted_char_field_decryption_failure(caplog):
    field = EncryptedCharField(max_length=255)

    # Valid encryption
    original_value = "secret_token"
    encrypted_value = field.encrypt(original_value)
    assert field.decrypt(encrypted_value) == original_value

    # Invalid encryption (bad token)
    invalid_token = "not-a-valid-token"  # noqa: S105
    with caplog.at_level("CRITICAL"):
        decrypted_value = field.decrypt(invalid_token)

    assert decrypted_value is None
    assert "Decryption failed: Invalid token" in caplog.text


@pytest.mark.django_db
def test_encrypted_char_field_different_key(caplog, settings):
    # Field initialized with current SECRET_KEY
    field = EncryptedCharField(max_length=255)
    original_value = "secret_token"
    encrypted_value = field.encrypt(original_value)

    # Change SECRET_KEY
    settings.SECRET_KEY = "completely-different-key"  # noqa: S105

    # Re-initialize field with new key (simulating a new process or server restart)
    new_field = EncryptedCharField(max_length=255)

    with caplog.at_level("CRITICAL"):
        decrypted_value = new_field.decrypt(encrypted_value)

    assert decrypted_value is None
    assert "Decryption failed: Invalid token" in caplog.text
