"""GPU benchmark test for Ghost Jarvis.

Compares Whisper STT latency on CPU vs GPU.
Run with: python test_gpu.py
"""
import time
import io
import wave
import numpy as np
import sys


def generate_test_audio(duration: float = 3.0, sample_rate: int = 16000) -> io.BytesIO:
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    audio = (
        np.sin(2 * np.pi * 440 * t) * 0.3
        + np.sin(2 * np.pi * 880 * t) * 0.2
        + np.sin(2 * np.pi * 1200 * t) * 0.1
    )
    audio = (audio * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(audio.tobytes())
    buf.seek(0)
    return buf


def benchmark():
    from faster_whisper import WhisperModel
    from gpu_utils import get_optimal_whisper_config, is_cuda_available

    print("=" * 50)
    print("Ghost Jarvis — GPU Benchmark")
    print("=" * 50)
    print(f"CUDA available: {is_cuda_available()}")
    if is_cuda_available():
        import torch
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        print(f"CUDA version: {torch.version.cuda}")
    print()

    audio = generate_test_audio()

    # CPU benchmark
    print("Loading Whisper on CPU...")
    model_cpu = WhisperModel("tiny", device="cpu", compute_type="int8", cpu_threads=4)
    audio.seek(0)
    print("Running inference on CPU...")
    start = time.perf_counter()
    segs, _ = model_cpu.transcribe(
        audio, language="es", beam_size=1, without_timestamps=True
    )
    list(segs)
    cpu_ms = (time.perf_counter() - start) * 1000
    print(f"  CPU latency: {cpu_ms:.1f} ms")
    del model_cpu

    # GPU benchmark
    if is_cuda_available():
        cfg = get_optimal_whisper_config(force_cpu=False)
        print(f"\nLoading Whisper on {cfg['device'].upper()}...")
        model_gpu = WhisperModel(
            "tiny",
            device=cfg["device"],
            compute_type=cfg["compute_type"],
            cpu_threads=cfg["cpu_threads"],
        )
        audio.seek(0)
        print("Running inference on GPU...")
        start = time.perf_counter()
        segs, _ = model_gpu.transcribe(
            audio, language="es", beam_size=1, without_timestamps=True
        )
        list(segs)
        gpu_ms = (time.perf_counter() - start) * 1000
        print(f"  GPU latency: {gpu_ms:.1f} ms")
        speedup = cpu_ms / gpu_ms if gpu_ms > 0 else 1.0
        print(f"\n  SPEEDUP: {speedup:.1f}x")
        del model_gpu
        import torch
        torch.cuda.empty_cache()
    else:
        print("\nGPU not available — skipping GPU benchmark")

    print("\n" + "=" * 50)
    print("Benchmark complete")
    print("=" * 50)


if __name__ == "__main__":
    benchmark()
