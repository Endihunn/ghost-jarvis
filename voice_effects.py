"""Voice effects pipeline for Ghost Jarvis — J.A.R.V.I.S. style.

Applies post-processing effects to TTS audio to achieve a robotic,
metallic, radio-like voice characteristic of J.A.R.V.I.S.

Uses pedalboard for professional audio effects.
"""
import hashlib
import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("voice")

try:
    from pedalboard import Pedalboard, Reverb, Delay, Compressor, Gain, Chorus, LadderFilter
    from pedalboard.io import AudioFile
    _HAS_PEDALBOARD = True
except ImportError:
    _HAS_PEDALBOARD = False
    logger.warning("pedalboard not installed — voice effects disabled")


def _build_jarvis_chain(
    reverb_wet: float = 0.15,
    delay_mix: float = 0.12,
    compressor: bool = True,
    chorus_mix: float = 0.2,
) -> Optional[Pedalboard]:
    """Build a pedalboard chain that mimics J.A.R.V.I.S. voice."""
    if not _HAS_PEDALBOARD:
        return None

    plugins = []

    if compressor:
        plugins.append(
            Compressor(
                threshold_db=-18.0,
                ratio=4.0,
                attack_ms=2.0,
                release_ms=50.0,
            )
        )

    plugins.append(
        LadderFilter(
            mode=LadderFilter.Mode.LPF12,
            cutoff_hz=3800,
            resonance=0.3,
        )
    )

    if chorus_mix > 0:
        plugins.append(
            Chorus(
                rate_hz=0.5,
                depth=0.15,
                centre_delay_ms=4.0,
                feedback=0.1,
                mix=chorus_mix,
            )
        )

    if delay_mix > 0:
        plugins.append(
            Delay(
                delay_seconds=0.08,
                feedback=0.15,
                mix=delay_mix,
            )
        )

    if reverb_wet > 0:
        plugins.append(
            Reverb(
                room_size=0.35,
                damping=0.6,
                wet_level=reverb_wet,
                dry_level=1.0 - reverb_wet * 0.5,
            )
        )

    plugins.append(Gain(gain_db=1.5))
    return Pedalboard(plugins)


def _apply_pitch_shift(audio: np.ndarray, sr: int, semitones: int) -> np.ndarray:
    """Pitch-shift audio using torchaudio. Returns audio unchanged if unavailable."""
    if semitones == 0:
        return audio
    try:
        import torch
        import torchaudio
        t = torch.from_numpy(audio) if audio.ndim == 1 else torch.from_numpy(audio)
        if t.ndim == 1:
            t = t.unsqueeze(0)
        shifted = torchaudio.functional.pitch_shift(t, sr, n_steps=float(semitones))
        return shifted.squeeze(0).numpy() if audio.ndim == 1 else shifted.numpy()
    except Exception:
        logger.warning("pitch_shift requires torchaudio, skipping (semitones=%d)", semitones)
        return audio


def process_audio_jarvis(
    input_path: Path,
    output_path: Path,
    reverb: float = 0.15,
    delay: float = 0.12,
    pitch_shift: int = -2,
    compressor: bool = True,
    chorus: float = 0.2,
    sample_rate: int = 24000,
) -> Path:
    """Process audio file through the J.A.R.V.I.S. effect chain."""
    if not _HAS_PEDALBOARD:
        import shutil
        shutil.copy(str(input_path), str(output_path))
        return output_path

    with AudioFile(str(input_path)) as f:
        audio = f.read(f.frames)
        in_sr = f.samplerate

    if audio.ndim > 1:
        audio = audio.mean(axis=0) if audio.shape[0] > 1 else audio[0]

    if in_sr != sample_rate:
        try:
            import torch
            import torchaudio
            t = torch.from_numpy(audio)
            resampled = torchaudio.functional.resample(t, in_sr, sample_rate)
            audio = resampled.numpy()
        except Exception:
            sample_rate = in_sr

    if pitch_shift != 0:
        audio = _apply_pitch_shift(audio, sample_rate, pitch_shift)

    chain = _build_jarvis_chain(
        reverb_wet=reverb,
        delay_mix=delay,
        compressor=compressor,
        chorus_mix=chorus,
    )
    if chain is not None:
        audio = chain(audio, sample_rate)

    peak = np.max(np.abs(audio))
    if peak > 1.0:
        audio = audio / peak * 0.98

    with AudioFile(str(output_path), "w", samplerate=sample_rate, num_channels=1) as f:
        f.write(audio)

    return output_path


def get_jarvis_wake_responses() -> list[str]:
    """Return formal, concise wake responses in J.A.R.V.I.S. style."""
    return [
        "¿Sí, señor?",
        "A sus órdenes",
        "Escuchando",
        "Sistemas en línea",
        "En espera de instrucciones",
        "Confirmado",
        "Atento",
        "¿En qué puedo asistirle?",
    ]


def jarvis_effect_hash(
    text: str,
    voice: str,
    rate: str,
    reverb: float,
    delay: float,
    pitch_shift: int,
    compressor: bool,
    chorus: float,
) -> str:
    """Return a cache key for processed TTS audio."""
    key = f"{voice}|{rate}|{reverb}|{delay}|{pitch_shift}|{compressor}|{chorus}|{text}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()
