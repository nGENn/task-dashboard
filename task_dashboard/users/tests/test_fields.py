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


@pytest.mark.django_db
def test_encrypted_char_field_decrypts_via_fallback_key(settings):
    """Rotating SECRET_KEY keeps old ciphertext readable when the previous key
    is listed in SECRET_KEY_FALLBACKS, and new writes use the new key."""
    old_key = "original-secret-key-value"
    settings.SECRET_KEY = old_key
    settings.SECRET_KEY_FALLBACKS = []
    old_field = EncryptedCharField(max_length=255)
    token = old_field.encrypt("secret_token")

    # Rotate: new primary key, old key demoted to a fallback.
    settings.SECRET_KEY = "rotated-secret-key-value"  # noqa: S105
    settings.SECRET_KEY_FALLBACKS = [old_key]
    rotated_field = EncryptedCharField(max_length=255)

    # Old ciphertext still decrypts via the fallback.
    assert rotated_field.decrypt(token) == "secret_token"
    # New writes round-trip under the rotated primary key.
    assert rotated_field.decrypt(rotated_field.encrypt("fresh")) == "fresh"
