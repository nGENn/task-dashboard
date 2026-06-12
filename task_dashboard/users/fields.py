import base64
import hashlib
import logging

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from cryptography.fernet import MultiFernet
from django.conf import settings
from django.db import models
from django.utils.encoding import force_bytes
from django.utils.encoding import force_str

logger = logging.getLogger(__name__)


def _fernet_for_secret(secret) -> Fernet:
    """Derive a Fernet from a secret via SHA-256 (32-byte key)."""
    key = hashlib.sha256(force_bytes(secret)).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class EncryptedCharField(models.CharField):
    """
    A field that encrypts data using cryptography.fernet.Fernet.

    The key is derived from settings.SECRET_KEY. To rotate the secret without
    losing access to existing data, set the old key(s) in
    ``settings.SECRET_KEY_FALLBACKS``: encryption always uses the current
    SECRET_KEY, while decryption tries the current key then each fallback
    (via MultiFernet). Re-saving a row migrates its ciphertext to the new key,
    after which the fallback can be dropped.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Current key encrypts; fallbacks only decrypt (enables key rotation).
        secrets = [
            settings.SECRET_KEY,
            *getattr(settings, "SECRET_KEY_FALLBACKS", []),
        ]
        self.fernet = MultiFernet([_fernet_for_secret(s) for s in secrets])

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return self.decrypt(value)

    def to_python(self, value):
        if value is None:
            return value
        return value

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return self.encrypt(force_str(value))

    def encrypt(self, value):
        if not value:
            return value
        # Ensure value is bytes for Fernet
        encrypted = self.fernet.encrypt(force_bytes(value))
        return force_str(encrypted)

    def decrypt(self, value):
        if not value:
            return value
        try:
            # Fernet.decrypt handles bytes or str (if str, it encodes to bytes)
            decrypted = self.fernet.decrypt(force_bytes(value))
            return force_str(decrypted)
        except InvalidToken:
            logger.critical(
                "Decryption failed: Invalid token. This usually means the SECRET_KEY "
                "has changed or the data is corrupted."
            )
            return None
        except Exception:
            logger.exception("Unexpected error during decryption")
            return None
