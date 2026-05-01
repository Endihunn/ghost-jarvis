"""Tests for config extra-field preservation, DPAPI round-trip, and schema migration."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as config_module
from config import Config
import secure_store


def test_extra_fields_preserved(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "gateway_url": "ws://127.0.0.1:18789",
                "privacy_mode": True,
                "custom_plugin_key": "secret_value",
                "another_extra": 42,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    original_path = config_module.CONFIG_PATH
    try:
        config_module.CONFIG_PATH = config_path
        cfg = Config.load()
        assert cfg.gateway_url == "ws://127.0.0.1:18789"
        assert cfg.privacy_mode is True
        assert cfg._extra.get("custom_plugin_key") == "secret_value"
        assert cfg._extra.get("another_extra") == 42

        cfg.save()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert data["custom_plugin_key"] == "secret_value"
        assert data["another_extra"] == 42
        # Known fields must still be present
        assert "gateway_url" in data
        assert "privacy_mode" in data
    finally:
        config_module.CONFIG_PATH = original_path


def test_dpapi_roundtrip():
    plain = "my-secret-token-123"
    encrypted = secure_store.encrypt(plain)
    if secure_store._DPAPI_OK:
        assert secure_store.is_encrypted(encrypted)
        assert encrypted != plain
        decrypted = secure_store.decrypt(encrypted)
        assert decrypted == plain
    else:
        # Fallback to plain text when DPAPI unavailable
        assert encrypted == plain


def test_dpapi_idempotent():
    plain = "token"
    encrypted = secure_store.encrypt(plain)
    double = secure_store.encrypt(encrypted)
    assert double == encrypted


def test_dpapi_empty_passthrough():
    assert secure_store.encrypt("") == ""
    assert secure_store.decrypt("") == ""


def test_schema_migration_adds_new_fields(tmp_path):
    """Old config missing new fields should be re-saved with defaults."""
    config_path = tmp_path / "config.json"
    # Simulate an old config missing 'privacy_mode' and '_extra'
    old_data = {
        "gateway_url": "ws://old:9000",
        "gateway_token": "",
        "session_key": "",
    }
    config_path.write_text(json.dumps(old_data, indent=2), encoding="utf-8")

    original_path = config_module.CONFIG_PATH
    try:
        config_module.CONFIG_PATH = config_path
        cfg = Config.load()
        # New fields get their defaults
        assert hasattr(cfg, "privacy_mode")
        assert cfg.privacy_mode is False
        # Re-save should have happened
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert "privacy_mode" in saved
        assert "session_key" in saved  # auto-generated
    finally:
        config_module.CONFIG_PATH = original_path


def test_is_encrypted():
    assert secure_store.is_encrypted("enc:v1:abc123") is True
    assert secure_store.is_encrypted("plain-text") is False
    assert secure_store.is_encrypted("") is False
    assert secure_store.is_encrypted(None) is False
