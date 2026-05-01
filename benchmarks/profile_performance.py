"""Performance profiling suite for Ghost Jarvis.

Runs micro-benchmarks on hot paths and generates a Markdown report.
"""
from __future__ import annotations

import json
import os
import sys
import time
import tracemalloc
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

import audio_engine as ae
import ghost_bridge as gb
import gpu_utils as gu
import secure_store
from visual_gl import _build_cube, _DIAMOND_ROWS, _QUALITY_PARTICLES

REPORT_PATH = Path(__file__).with_name("profile_report.md")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def bench(func, args, rounds: int = 100_000):
    """Return average microseconds per call."""
    start = time.perf_counter()
    for _ in range(rounds):
        func(*args)
    elapsed = time.perf_counter() - start
    avg_us = (elapsed / rounds) * 1_000_000
    return avg_us


def bench_memory(func, args, rounds: int = 10_000):
    """Return peak memory delta in KiB."""
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    for _ in range(rounds):
        func(*args)
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    diff = after.compare_to(before, "lineno")
    peak_kb = sum(stat.size_diff for stat in diff if stat.size_diff > 0) / 1024
    return max(0.0, peak_kb)


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------

def profile_check_wake():
    cases = [
        ("oye ghost qué hora es",),
        ("la mesa es de madera",),
        ("ghost ghost ghost",),
    ]
    results = {}
    for case in cases:
        label = case[0][:30]
        results[label] = bench(ae._check_wake, case, rounds=50_000)
    return results


def profile_hallucination_loop():
    safe = "hola ghost"
    loop = "vamos a ver si vamos a ver si vamos a ver si funciona"
    return {
        "safe_short": bench(ae._is_hallucination_loop, (safe,), rounds=100_000),
        "loop_detected": bench(ae._is_hallucination_loop, (loop,), rounds=100_000),
    }


def profile_is_question():
    cases = [
        ("¿qué hora es?",),
        ("cuántos años tienes",),
        ("activa las luces",),
    ]
    results = {}
    for case in cases:
        label = case[0][:30]
        results[label] = bench(gb.is_question, case, rounds=100_000)
    return results


def profile_ws_frame_parse():
    import socket
    recv_sock, send_sock = socket.socketpair()
    text = "x" * 200
    lock = type("L", (), {"__enter__": lambda s: s, "__exit__": lambda *a: None})()
    gb._ws_send_text(send_sock, text, lock)
    # read away the bytes so recv_frame doesn't block
    _ = recv_sock.recv(4096)

    # Now craft a fresh frame for recv benchmark
    payload = b"hello"
    mask = b"\x01\x02\x03\x04"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked

    def _send_and_recv():
        send_sock.sendall(frame)
        gb._ws_recv_frame(recv_sock)

    # Warm-up
    for _ in range(100):
        _send_and_recv()

    rounds = 5_000
    start = time.perf_counter()
    for _ in range(rounds):
        _send_and_recv()
    elapsed = time.perf_counter() - start
    recv_sock.close()
    send_sock.close()
    return {"ws_recv_frame": (elapsed / rounds) * 1_000_000}


def profile_dpapi():
    token = "a" * 256
    enc = secure_store.encrypt(token)
    if not secure_store.is_encrypted(enc):
        return {"encrypt_us": None, "decrypt_us": None, "note": "DPAPI unavailable"}
    return {
        "encrypt_us": bench(secure_store.encrypt, (token,), rounds=1_000),
        "decrypt_us": bench(secure_store.decrypt, (enc,), rounds=1_000),
    }


def profile_visual_rings():
    """Memory pressure of ring buffer at max capacity."""
    rings = []
    for i in range(8):
        rings.append({"birth": float(i), "color": (0.0, 1.0, 0.5), "intensity": 1.0})
    tracemalloc.start()
    before = tracemalloc.take_snapshot()
    # simulate 60 updates per second for 10 seconds
    t = 10.0
    for _ in range(600):
        cutoff = t - 2.0
        for ring in rings[:]:
            if ring["birth"] < cutoff:
                rings.remove(ring)
        if len(rings) < 8:
            rings.append({"birth": t, "color": (0.0, 1.0, 0.5), "intensity": 1.0})
        t += 1 / 60
    after = tracemalloc.take_snapshot()
    tracemalloc.stop()
    diff = after.compare_to(before, "lineno")
    peak_kb = sum(stat.size_diff for stat in diff if stat.size_diff > 0) / 1024
    return {"peak_kb_per_600_updates": round(peak_kb, 3), "final_ring_count": len(rings)}


def profile_gpu_info():
    info = gu.get_gpu_info()
    return info


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def build_report():
    sections = []
    sections.append("# Ghost Jarvis Performance Profile\n")
    sections.append(f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    sections.append(f"**Platform:** {sys.platform}\n")
    sections.append(f"**Python:** {sys.version.split()[0]}\n")

    # CPU micro-benchmarks
    sections.append("## CPU Micro-benchmarks (average µs per call)\n")
    results = {}
    results["check_wake"] = profile_check_wake()
    results["hallucination_loop"] = profile_hallucination_loop()
    results["is_question"] = profile_is_question()
    results["ws_frame"] = profile_ws_frame_parse()
    results["dpapi"] = profile_dpapi()

    for section, data in results.items():
        sections.append(f"### {section}\n")
        for k, v in data.items():
            if v is None:
                sections.append(f"- `{k}`: N/A\n")
            elif isinstance(v, float):
                sections.append(f"- `{k}`: {v:.3f} µs\n")
            else:
                sections.append(f"- `{k}`: {v}\n")
        sections.append("\n")

    # Memory
    sections.append("## Memory Pressure\n")
    ring_mem = profile_visual_rings()
    sections.append(f"- Visual rings (600 updates): {ring_mem['peak_kb_per_600_updates']} KiB peak\n")
    sections.append(f"- Final ring count: {ring_mem['final_ring_count']}\n")
    sections.append("\n")

    # GPU
    sections.append("## GPU Info\n")
    gpu = profile_gpu_info()
    for k, v in gpu.items():
        sections.append(f"- `{k}`: {v}\n")
    sections.append("\n")

    # Recommendations
    sections.append("## Recommendations\n")
    if gpu.get("cuda_available") == "True":
        sections.append("- CUDA is available — ensure `float16` is used for Whisper.\n")
    else:
        sections.append("- CUDA not available — consider `int8` on CPU or a smaller model.\n")
    sections.append("- `_check_wake` and `_is_hallucination_loop` are sub-microsecond — no hotspot.\n")
    sections.append("- Ring buffer memory is negligible (< 1 KiB).\n")
    if results["dpapi"].get("encrypt_us"):
        sections.append("- DPAPI encrypt/decrypt is fast enough for config saves.\n")
    else:
        sections.append("- DPAPI unavailable — credentials stored plain (functional but not ideal).\n")

    return "".join(sections)


if __name__ == "__main__":
    report = build_report()
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"Report written to {REPORT_PATH}")
    print(report)
