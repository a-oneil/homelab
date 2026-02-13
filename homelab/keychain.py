"""Cross-platform secret storage using Fernet symmetric encryption.

Secrets are encrypted with a key stored in ~/.homelab/encryption.key (mode 0600).
The key file is auto-generated on first use. The config file stores
encrypted values with a sentinel prefix so they can be identified.

Requires: pip install cryptography
"""

import base64
import os
import stat

_HOMELAB_DIR = os.path.expanduser("~/.homelab")
_KEY_PATH = os.path.join(_HOMELAB_DIR, "encryption.key")
_FERNET = None  # Lazy-loaded


def _get_fernet():
    """Get or create the Fernet cipher, generating a key file if needed."""
    global _FERNET
    if _FERNET is not None:
        return _FERNET

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None

    if os.path.exists(_KEY_PATH):
        with open(_KEY_PATH, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        os.makedirs(_HOMELAB_DIR, exist_ok=True)
        # Write key with restricted permissions
        fd = os.open(_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, key + b"\n")
        finally:
            os.close(fd)

    try:
        _FERNET = Fernet(key)
    except Exception:
        return None

    # Ensure permissions are correct even on existing files
    try:
        os.chmod(_KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    return _FERNET


def is_available():
    """Return True if encryption is available."""
    return _get_fernet() is not None


def store(key, value):
    """Encrypt a value. Returns the encrypted string, or None on failure."""
    f = _get_fernet()
    if not f or not value:
        return None
    try:
        encrypted = f.encrypt(value.encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")
    except Exception:
        return None


def retrieve(key, encrypted_value):
    """Decrypt a value. Returns the plaintext string, or None on failure."""
    f = _get_fernet()
    if not f or not encrypted_value:
        return None
    try:
        token = base64.urlsafe_b64decode(encrypted_value.encode("ascii"))
        return f.decrypt(token).decode("utf-8")
    except Exception:
        return None
