"""Tests for gpu_utils CUDA detection and whisper config."""
import sys
from pathlib import Path
import os

sys.path.insert(0, str(Path(__file__).parent.parent))

import gpu_utils as gu


def test_get_gpu_info_without_cuda():
    info = gu.get_gpu_info()
    assert "cuda_available" in info
    assert "device_name" in info
    assert "cuda_version" in info


def test_get_optimal_whisper_config_cpu_fallback(monkeypatch):
    monkeypatch.setenv("GHOST_WHISPER_DEVICE", "cpu")
    # Force re-detection by resetting cached globals
    import importlib
    import gpu_utils as gu2
    gu2._CUDA_AVAILABLE = False
    gu2._CUDA_DEVICE_NAME = None
    gu2._CUDA_VERSION = None
    cfg = gu2.get_optimal_whisper_config(force_cpu=False, model_size="tiny")
    assert cfg["device"] == "cpu"
    assert cfg["compute_type"] == "int8"
    assert cfg["gpu_index"] is None
    assert cfg["cpu_threads"] >= 4


def test_get_optimal_whisper_config_model_sizes():
    import gpu_utils as gu2
    gu2._CUDA_AVAILABLE = False
    gu2._CUDA_DEVICE_NAME = None
    gu2._CUDA_VERSION = None
    cfg_tiny = gu2.get_optimal_whisper_config(force_cpu=True, model_size="tiny")
    assert cfg_tiny["device"] == "cpu"
    cfg_large = gu2.get_optimal_whisper_config(force_cpu=True, model_size="large-v3")
    assert cfg_large["device"] == "cpu"


def test_get_optimal_whisper_config_env_override(monkeypatch):
    monkeypatch.setenv("GHOST_WHISPER_DEVICE", "cpu")
    import gpu_utils as gu2
    gu2._CUDA_AVAILABLE = True
    gu2._CUDA_DEVICE_NAME = "Fake GPU"
    gu2._CUDA_VERSION = "12.1"
    cfg = gu2.get_optimal_whisper_config(force_cpu=False, model_size="tiny")
    assert cfg["device"] == "cpu"


def test_get_vram_usage_without_cuda():
    import gpu_utils as gu2
    gu2._CUDA_AVAILABLE = False
    assert gu2.get_vram_usage_mb() is None
