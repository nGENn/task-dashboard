import base64
import hashlib

from django.conf import settings
from django.db import models
from django.utils.encoding import force_bytes, force_str
from cryptography.fernet import Fernet


class EncryptedCharField(models.CharField):
    """
    A field that encrypts data using cryptography.fernet.Fernet.
    The key is derived from settings.SECRET_KEY.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Derive a 32-byte base64-encoded key from SECRET_KEY
        key = hashlib.sha256(force_bytes(settings.SECRET_KEY)).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(key))

    def from_db_value(self, value, expression, connection):
        if value is None:
            return value
        return self.decrypt(value)

    def to_python(self, value):
        if value is None:
            return value
        return self.decrypt(value)

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
        except Exception:
            # If decryption fails, return the original value
            # This handles cases where data might already be decrypted
            # or invalid.
            return value
