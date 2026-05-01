"""GPU detection and optimization utilities for Ghost Jarvis.

Auto-detects CUDA availability and returns optimal Whisper configuration.
Also provides VRAM monitoring and benchmark utilities.
"""
import logging
import os
from typing import Dict, Optional

logger = logging.getLogger("gpu")

_CUDA_AVAILABLE: Optional[bool] = None
_CUDA_DEVICE_NAME: Optional[str] = None
_CUDA_VERSION: Optional[str] = None


def _detect_cuda() -> bool:
    """Detect if CUDA is available via torch."""
    global _CUDA_AVAILABLE, _CUDA_DEVICE_NAME, _CUDA_VERSION
    if _CUDA_AVAILABLE is not None:
        return _CUDA_AVAILABLE
    try:
        import torch
        _CUDA_AVAILABLE = torch.cuda.is_available()
        if _CUDA_AVAILABLE:
            _CUDA_DEVICE_NAME = torch.cuda.get_device_name(0)
            _CUDA_VERSION = torch.version.cuda
            logger.info(
                "CUDA detected: %s (CUDA %s)", _CUDA_DEVICE_NAME, _CUDA_VERSION
            )
        else:
            logger.info("CUDA not available, falling back to CPU")
    except ImportError:
        logger.warning("torch not installed, using CPU")
        _CUDA_AVAILABLE = False
    return _CUDA_AVAILABLE


def is_cuda_available() -> bool:
    return _detect_cuda()


def get_gpu_info() -> Dict[str, Optional[str]]:
    _detect_cuda()
    return {
        "cuda_available": str(_CUDA_AVAILABLE),
        "device_name": _CUDA_DEVICE_NAME or "N/A",
        "cuda_version": _CUDA_VERSION or "N/A",
    }


def get_optimal_whisper_config(
    force_cpu: bool = False,
    model_size: str = "tiny",
) -> Dict[str, object]:
    """Return optimal WhisperModel parameters for this system."""
    env_device = os.environ.get("GHOST_WHISPER_DEVICE", "")
    if env_device:
        force_cpu = env_device.lower() == "cpu"

    cpu_count = os.cpu_count() or 4

    if not force_cpu and is_cuda_available():
        # Honour explicit user choice from config when valid; otherwise pick by model.
        from config import APP_CONFIG  # lazy import to avoid circular at module load
        valid = {"float16", "int8_float16", "int8", "float32"}
        configured = (APP_CONFIG.gpu_compute_type or "").strip()
        if configured in valid:
            compute_type = configured
        else:
            compute_type = "float16"
            if model_size == "medium":
                compute_type = "int8_float16"
            elif model_size in ("large-v1", "large-v2", "large-v3"):
                compute_type = "int8"
        return {
            "device": "cuda",
            "compute_type": compute_type,
            "cpu_threads": max(1, cpu_count // 2),
            "gpu_index": 0,
        }
    else:
        return {
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": max(4, cpu_count - 2),
            "gpu_index": None,
        }


def get_vram_usage_mb() -> Optional[float]:
    """Return current VRAM usage in MB, or None if not available."""
    if not is_cuda_available():
        return None
    try:
        import torch
        return torch.cuda.memory_allocated(0) / (1024 * 1024)
    except Exception:
        return None
