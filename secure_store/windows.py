"""Windows DPAPI-backed credential encryption."""
import base64
import logging

logger = logging.getLogger("secure_store")

ENC_PREFIX = "enc:v1:"
_ENTROPY = b"ghost-jarvis/v1"

try:
    import win32crypt  # type: ignore
    _DPAPI_OK = True
except Exception:
    win32crypt = None  # type: ignore
    _DPAPI_OK = False


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt(value: str) -> str:
    if not value or is_encrypted(value):
        return value
    if not _DPAPI_OK:
        logger.warning("DPAPI unavailable — credential stored in plain text.")
        return value
    try:
        blob = win32crypt.CryptProtectData(
            value.encode("utf-8"), "ghost-jarvis", _ENTROPY, None, None, 0
        )
        return ENC_PREFIX + base64.b64encode(blob).decode("ascii")
    except Exception as e:
        logger.warning("DPAPI encrypt failed (%s) — storing plain.", e)
        return value


def decrypt(value: str) -> str:
    if not value or not is_encrypted(value):
        return value
    if not _DPAPI_OK:
        logger.error("DPAPI unavailable — cannot decrypt %s...", value[:14])
        return ""
    try:
        blob = base64.b64decode(value[len(ENC_PREFIX):])
        _desc, plain = win32crypt.CryptUnprotectData(blob, _ENTROPY, None, None, 0)
        return plain.decode("utf-8")
    except Exception as e:
        logger.error("DPAPI decrypt failed: %s", e)
        return ""
