"""Audio pipeline test for Ghost Jarvis.

Validates voice effect processing and TTS pipeline.
Run with: python test_audio.py
"""
import tempfile
from pathlib import Path
import numpy as np


def test_voice_effects():
    print("=" * 50)
    print("Ghost Jarvis — Audio Pipeline Test")
    print("=" * 50)

    from voice_effects import (
        _build_jarvis_chain,
        _apply_pitch_shift,
        get_jarvis_wake_responses,
        process_audio_jarvis,
    )

    # Test wake responses
    responses = get_jarvis_wake_responses()
    print(f"\nWake responses ({len(responses)}):")
    for r in responses[:3]:
        print(f"  - {r}")

    # Test pitch shift
    print("\nTesting pitch shift...")
    sr = 24000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    audio = np.sin(2 * np.pi * 440 * t).astype(np.float32) * 0.5
    shifted = _apply_pitch_shift(audio, sr, -2)
    assert shifted.shape == audio.shape, "Pitch shift changed audio length"
    print("  Pitch shift: OK")

    # Test effect chain
    print("\nTesting J.A.R.V.I.S. effect chain...")
    chain = _build_jarvis_chain()
    if chain is not None:
        out = chain(audio.reshape(1, -1), sr)
        assert out.shape[1] == audio.shape[0], "Effect chain changed audio length"
        print("  Effect chain: OK")
    else:
        print("  Effect chain: SKIP (pedalboard not installed)")

    # Test full pipeline with dummy WAV
    print("\nTesting full pipeline with dummy WAV...")
    import wave
    import io

    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        pcm = (audio * 32767).astype(np.int16)
        w.writeframes(pcm.tobytes())
    wav_buf.seek(0)

    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "input.wav"
        out_path = Path(tmp) / "output.wav"
        in_path.write_bytes(wav_buf.read())
        result = process_audio_jarvis(in_path, out_path)
        assert result.exists(), "Output file not created"
        assert result.stat().st_size > 0, "Output file is empty"
        print("  Full pipeline: OK")

    print("\n" + "=" * 50)
    print("Audio test complete")
    print("=" * 50)


if __name__ == "__main__":
    test_voice_effects()
