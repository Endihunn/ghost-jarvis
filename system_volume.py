"""Windows per-session volume ducking using pycaw.

Lowers the volume of OTHER audio sessions while Ghost Jarvis is speaking,
leaving Jarvis's own voice (pygame / pyttsx3 / edge-tts) at full level.
"""
import logging
import os
from typing import Dict

logger = logging.getLogger("volume")

# Lazy import so the module is safe to import even when pycaw is missing
try:
    from pycaw.pycaw import AudioUtilities
    _HAS_PYCAW = True
except Exception as exc:
    _HAS_PYCAW = False
    logger.warning("pycaw not available (%s); per-session volume ducking disabled", exc)


class _VolumeState:
    # instance_id -> original master volume
    _saved: Dict[str, float] = {}
    _ducked = False


def _our_pid() -> int:
    return os.getpid()


def _list_sessions():
    """Yield (session, instance_id, pid, name) for all audio sessions."""
    if not _HAS_PYCAW:
        return
    try:
        sessions = AudioUtilities.GetAllSessions()
    except Exception as e:
        logger.error("Failed to enumerate audio sessions: %s", e)
        return
    for session in sessions:
        try:
            pid = session.ProcessId
        except Exception:
            pid = 0
        try:
            instance_id = session.InstanceIdentifier
        except Exception:
            instance_id = f"pid:{pid}"
        name = None
        try:
            if session.Process is not None:
                name = session.Process.name()
        except Exception:
            pass
        yield session, instance_id, pid, name


def save_and_duck(duck_level: float) -> None:
    """Save volume of foreign sessions and lower them to *duck_level*.

    Sessions belonging to the current process (Jarvis itself) are never touched.
    If already ducked, only newly-appeared sessions are adjusted.
    """
    if not _HAS_PYCAW:
        return

    duck_level = max(0.0, min(1.0, duck_level))
    our_pid = _our_pid()

    if _VolumeState._ducked:
        # Already ducked — catch any new sessions that started while we were talking
        for session, instance_id, pid, name in _list_sessions():
            if pid == our_pid:
                continue
            if instance_id in _VolumeState._saved:
                continue
            try:
                vol = session.SimpleAudioVolume.GetMasterVolume()
                _VolumeState._saved[instance_id] = vol
                session.SimpleAudioVolume.SetMasterVolume(duck_level, None)
                logger.debug(
                    "Ducked new session %s (%s): %.0f%% -> %.0f%%",
                    name, instance_id, vol * 100, duck_level * 100,
                )
            except Exception as e:
                logger.debug("Could not duck session %s: %s", instance_id, e)
        return

    _VolumeState._saved.clear()
    for session, instance_id, pid, name in _list_sessions():
        if pid == our_pid:
            continue
        try:
            vol = session.SimpleAudioVolume.GetMasterVolume()
            _VolumeState._saved[instance_id] = vol
            session.SimpleAudioVolume.SetMasterVolume(duck_level, None)
            logger.debug(
                "Ducked session %s (%s): %.0f%% -> %.0f%%",
                name, instance_id, vol * 100, duck_level * 100,
            )
        except Exception as e:
            logger.debug("Could not duck session %s: %s", instance_id, e)

    if _VolumeState._saved:
        logger.info(
            "Ducked %d foreign session(s) to %.0f%%",
            len(_VolumeState._saved), duck_level * 100,
        )
    _VolumeState._ducked = True


def restore_volume() -> None:
    """Restore previously saved per-session volumes."""
    if not _HAS_PYCAW:
        return
    if not _VolumeState._ducked:
        return

    our_pid = _our_pid()
    restored = 0

    for session, instance_id, pid, name in _list_sessions():
        if pid == our_pid:
            continue
        if instance_id not in _VolumeState._saved:
            continue
        try:
            original = _VolumeState._saved.pop(instance_id)
            session.SimpleAudioVolume.SetMasterVolume(original, None)
            restored += 1
            logger.debug(
                "Restored session %s (%s) to %.0f%%",
                name, instance_id, original * 100,
            )
        except Exception as e:
            logger.debug("Could not restore session %s: %s", instance_id, e)

    # Sessions that disappeared while ducked — just forget them
    if _VolumeState._saved:
        logger.debug(
            "Could not restore %d session(s) (already closed)",
            len(_VolumeState._saved),
        )
        _VolumeState._saved.clear()

    if restored:
        logger.info("Restored volume for %d session(s)", restored)
    _VolumeState._ducked = False


def get_system_volume() -> float:
    """Return current master volume in range [0.0, 1.0]."""
    if not _HAS_PYCAW:
        return 1.0
    try:
        device = AudioUtilities.GetSpeakers()
        return device.EndpointVolume.GetMasterVolumeLevelScalar()
    except Exception as e:
        logger.error("get_system_volume error: %s", e)
        return 1.0


def set_system_volume(level: float) -> None:
    """Set master volume to *level* (clamped 0.0-1.0)."""
    level = max(0.0, min(1.0, level))
    if not _HAS_PYCAW:
        return
    try:
        device = AudioUtilities.GetSpeakers()
        device.EndpointVolume.SetMasterVolumeLevelScalar(level, None)
        logger.debug("System volume set to %.0f%%", level * 100)
    except Exception as e:
        logger.error("set_system_volume error: %s", e)
