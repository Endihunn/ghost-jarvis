"""macOS / Linux credential encryption using Fernet + machine-bound key.

The key is derived from a stable machine identifier:
  - Linux: /etc/machine-id (or /var/lib/dbus/machine-id)
  - macOS: IOPlatformUUID via ioreg

If no machine identifier is available, falls back to uuid.getnode() + $HOME.
The derived key is salted with a fixed application salt and run through
PBKDF2HMAC (100k iterations) to produce the Fernet key.
"""
import base64
import hashlib
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger("secure_store")

ENC_PREFIX = "enc:v1:"
_APP_SALT = b"ghost-jarvis/v1-unix"


def _get_machine_id() -> bytes:
    """Return a stable machine identifier as bytes."""
    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            for line in output.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split('"')
                    if len(parts) >= 4:
                        return parts[-2].encode("utf-8")
        except Exception:
            pass
    # Linux
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(path).read_bytes().strip()
        except Exception:
            pass
    # Fallback
    home = os.environ.get("HOME", "").encode("utf-8")
    node = str(uuid.getnode()).encode("utf-8")
    return hashlib.sha256(home + node).digest()


def _derive_key() -> bytes:
    machine_id = _get_machine_id()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_APP_SALT,
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(machine_id))


def _get_fernet() -> Fernet:
    return Fernet(_derive_key())


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(value: str) -> str:
    if not value or is_encrypted(value):
        return value
    try:
        token = _get_fernet().encrypt(value.encode("utf-8"))
        return ENC_PREFIX + token.decode("ascii")
    except Exception as e:
        logger.warning("Encrypt failed (%s) — storing plain.", e)
        return value


def decrypt(value: str) -> str:
    if not value or not is_encrypted(value):
        return value
    try:
        token = value[len(ENC_PREFIX):].encode("ascii")
        return _get_fernet().decrypt(token).decode("utf-8")
    except Exception as e:
        logger.error("Decrypt failed: %s", e)
        return ""
