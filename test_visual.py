"""Visual/shader test for Ghost Jarvis.

Validates that all shaders compile correctly.
Run with: python test_visual.py
"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer


def test_visual():
    print("=" * 50)
    print("Ghost Jarvis — Visual/Shaders Test")
    print("=" * 50)

    app = QApplication.instance() or QApplication(sys.argv)

    from visual_gl import VisualGLWidget

    print("\nCreating VisualGLWidget...")
    vw = VisualGLWidget()
    vw.show()
    vw.hide()

    # Verify all shader programs compiled
    print(f"  Cube shader program: {vw._prog}")
    print(f"  Particle shader program: {vw._particle_prog}")
    print(f"  Grid shader program: {vw._grid_prog}")
    print(f"  Ring shader program: {vw._ring_prog}")

    assert vw._prog != 0, "Cube shader failed to compile"
    assert vw._particle_prog != 0, "Particle shader failed to compile"
    assert vw._grid_prog != 0, "Grid shader failed to compile"
    assert vw._ring_prog != 0, "Ring shader failed to compile"

    print("\nTesting state transitions...")
    states = ["IDLE", "WAKE", "LISTENING", "PROCESSING", "SPEAKING", "STANDBY"]
    for s in states:
        vw.set_state(s)
        assert vw._state == s, f"State {s} not set correctly"
        print(f"  State {s}: OK")

    print("\nTesting audio reactivity...")
    vw.set_audio_volume(0.5)
    assert vw._audio_vol == 0.5
    vw.set_speech_volume(0.8)
    assert vw._speech_vol == 0.8
    vw.set_audio_spectrum([0.1, 0.2, 0.3, 0.4, 0.5, 0.4, 0.3, 0.2])
    assert len(vw._spectrum) == 8
    print("  Audio reactivity: OK")

    vw.close()

    print("\n" + "=" * 50)
    print("Visual test complete — all shaders compiled OK")
    print("=" * 50)


if __name__ == "__main__":
    test_visual()
