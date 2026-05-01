"""Configuration manager for Ghost Jarvis.

Loads/saves config.json in the user's platform-specific config directory.
Sensitive fields (gateway_token, session_key) are transparently encrypted
on disk via the platform's native credential store — see secure_store.
"""
import json
import logging
import os
import sys
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from platformdirs import user_config_dir, user_data_dir
from secure_store import encrypt, decrypt, is_encrypted

APP_NAME = "GhostJarvis"
APP_AUTHOR = "GhostLabs"

CONFIG_DIR = Path(user_config_dir(APP_NAME, APP_AUTHOR))
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = CONFIG_DIR / "config.json"

_SECRET_FIELDS = ("gateway_token", "session_key")


def _default_openclaw_cmd() -> str:
    if sys.platform == "win32":
        npm_dir = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "npm"
        return str(npm_dir / "openclaw.cmd")
    return str(Path.home() / ".npm" / "bin" / "openclaw")


def _default_openclaw_config() -> str:
    return str(Path.home() / ".openclaw" / "openclaw.json")


@dataclass
class Config:
    # Ghost bridge settings (OpenClaw)
    openclaw_cmd: str = field(default_factory=_default_openclaw_cmd)
    openclaw_config: str = field(default_factory=_default_openclaw_config)
    ghost_prompt_prefix: str = ""

    # Gateway connection
    gateway_url: str = "ws://127.0.0.1:18789"
    gateway_token: str = ""
    session_key: str = ""

    # Overlay geometry
    overlay_width: int = 420
    overlay_height: int = 420
    overlay_centered: bool = True
    overlay_x: Optional[int] = None
    overlay_y: Optional[int] = None

    # Audio / Wake
    wake_phrases: List[str] = field(default_factory=lambda: [
        "oye ghost", "oiga ghost", "ey ghost", "ei ghost", "ghost", "gost",
    ])
    mic_gain: float = 2.5
    mic_auto_gain: bool = True
    mic_target_level: float = 0.15

    # VAD / STT advanced
    vad_aggressiveness: int = 2
    silence_timeout_ms: int = 450
    min_utterance_ms: int = 250
    ring_buffer_ms: int = 600
    wake_fuzz_threshold: int = 78

    # GPU
    gpu_enabled: bool = True
    gpu_compute_type: str = "float16"

    # TTS: False = pyttsx3 instantáneo, True = edge-tts (mejor calidad, más lento)
    tts_use_edge: bool = True
    tts_voice: str = "es-MX-JorgeNeural"
    tts_rate: str = "+5%"
    tts_local_rate: int = 185

    # Voz Jarvis
    jarvis_voice_effects: bool = True
    jarvis_reverb: float = 0.15
    jarvis_delay: float = 0.12
    jarvis_pitch_shift: int = -2
    jarvis_compressor: bool = True
    jarvis_chorus: float = 0.2

    # System volume ducking while Ghost is active
    duck_volume_enabled: bool = True
    duck_volume_level: float = 0.08

    # Visual
    visual_fps: int = 60
    visual_quality: str = "high"
    particles_enabled: bool = True
    scanlines_enabled: bool = True
    grid_enabled: bool = True
    wireframe_enabled: bool = True
    glitch_enabled: bool = True

    # Privacy
    privacy_mode: bool = False

    # Extra fields (preserves unknown keys from config.json)
    _extra: dict = field(default_factory=dict)

    def save(self):
        data = asdict(self)
        extra = data.pop("_extra", {})
        for fname in _SECRET_FIELDS:
            if data.get(fname):
                data[fname] = encrypt(data[fname])
        # Merge extras so custom keys are preserved on disk
        if extra and isinstance(extra, dict):
            data.update(extra)
        try:
            CONFIG_PATH.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logging.getLogger("config").warning("Could not save: %s", e)

    @classmethod
    def load(cls) -> "Config":
        import uuid as _uuid
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.session_key = f"agent:main:{_uuid.uuid4().hex[:8]}"
            cfg.save()
            return cfg
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            had_plaintext_secret = any(
                data.get(f) and not is_encrypted(data[f]) for f in _SECRET_FIELDS
            )
            for fname in _SECRET_FIELDS:
                if data.get(fname):
                    data[fname] = decrypt(data[fname])
            field_names = {f.name for f in cls.__dataclass_fields__.values()}
            known = {k: v for k, v in data.items() if k in field_names}
            extra = {k: v for k, v in data.items() if k not in field_names}
            cfg = cls(**known)
            cfg._extra = extra
            # Re-save if (a) the schema added new fields, (b) session_key was
            # missing, or (c) any secret was still plain text on disk.
            needs_save = had_plaintext_secret
            if not cfg.session_key:
                cfg.session_key = f"agent:main:{_uuid.uuid4().hex[:8]}"
                needs_save = True
            if set(known.keys()) != field_names:
                needs_save = True
            if needs_save:
                cfg.save()
            return cfg
        except Exception as e:
            logging.getLogger("config").warning("Could not load (%s), using defaults.", e)
            return cls()


# Global instance
APP_CONFIG = Config.load()
