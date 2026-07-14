"""Tests for visual_gl ring management and cube geometry."""
import sys
from pathlib import Path
import math

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from visual_gl import _build_cube, _DIAMOND_ROWS, _QUALITY_PARTICLES


class DummyVisual:
    """Minimal stand-in for VisualGLWidget to test ring logic."""

    def __init__(self):
        self._time = 0.0
        self._rings: list[dict] = []
        self._last_ring_time = -10.0

    def _update_rings(self):
        cutoff = self._time - 2.0
        for ring in self._rings[:]:
            if ring["birth"] < cutoff:
                self._rings.remove(ring)

    def _spawn_ring(self, color, intensity=1.0):
        if len(self._rings) >= 8:
            return
        if (self._time - self._last_ring_time) < 0.2:
            return
        self._last_ring_time = self._time
        self._rings.append({"birth": self._time, "color": color, "intensity": intensity})


def test_build_cube():
    verts, norms = _build_cube()
    assert verts.shape == (36, 3)
    assert norms.shape == (36, 3)
    assert verts.dtype == np.float32
    assert norms.dtype == np.float32


def test_diamond_rows_total_cubes():
    total = sum(len(row) for row in _DIAMOND_ROWS)
    assert total == 9


def test_quality_particles_mapping():
    assert _QUALITY_PARTICLES["low"] == 16
    assert _QUALITY_PARTICLES["medium"] == 32
    assert _QUALITY_PARTICLES["high"] == 64


def test_ring_expiration():
    dv = DummyVisual()
    dv._time = 5.0
    dv._rings.append({"birth": 2.5, "color": "cyan", "intensity": 1.0})
    dv._rings.append({"birth": 3.1, "color": "red", "intensity": 1.0})
    dv._update_rings()
    assert len(dv._rings) == 1
    assert dv._rings[0]["color"] == "red"


def test_ring_no_expiration():
    dv = DummyVisual()
    dv._time = 5.0
    dv._rings.append({"birth": 3.1, "color": "red", "intensity": 1.0})
    dv._update_rings()
    assert len(dv._rings) == 1


def test_spawn_ring_respects_cap():
    dv = DummyVisual()
    dv._last_ring_time = -10.0
    for i in range(10):
        dv._time = 10.0 + i * 0.3
        dv._spawn_ring("cyan")
    assert len(dv._rings) == 8


def test_spawn_ring_respects_rate_limit():
    dv = DummyVisual()
    dv._time = 1.0
    dv._last_ring_time = 0.95
    dv._spawn_ring("cyan")
    assert len(dv._rings) == 0
    dv._time = 1.21
    dv._spawn_ring("cyan")
    assert len(dv._rings) == 1


def test_spawn_ring_updates_last_time():
    dv = DummyVisual()
    dv._time = 5.0
    dv._spawn_ring("green")
    assert dv._last_ring_time == 5.0
