"""Cross-platform secure credential storage for Ghost Jarvis.

Delegates to the best available backend per platform:
  - Windows: DPAPI (CryptProtectData)
  - macOS / Linux: AES-256-GCM via Fernet with a key derived from the
    machine identifier (IOPlatformUUID on macOS, /etc/machine-id on Linux).
"""
import sys

if sys.platform == "win32":
    from .windows import encrypt, decrypt, is_encrypted, _DPAPI_OK
else:
    from .unix import encrypt, decrypt, is_encrypted
    _DPAPI_OK = False

__all__ = ["encrypt", "decrypt", "is_encrypted", "_DPAPI_OK"]
