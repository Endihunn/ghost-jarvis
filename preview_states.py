"""Render one frame of the real VisualGLWidget per state to PNG.

Dev tool to tune the look without driving the voice pipeline.
Run: .venv\\Scripts\\python preview_states.py

Caveat: grabFramebuffer + QPainter compose shows color-fringing artifacts
on the bloom rim (red edges on cyan, etc.) that do NOT appear in the real
DWM-composited window. Use a real screen capture as ground truth for
color; use this tool for layout/balance between states.
"""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QColor


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    from visual_gl import VisualGLWidget

    vw = VisualGLWidget()
    vw.resize(320, 320)
    vw.show()
    app.processEvents()

    from PyQt6.QtGui import QVector3D
    from visual_gl import _COLORS, _HALO_COLORS, _COLORS2

    states = ["IDLE", "LISTENING", "PROCESSING", "SPEAKING", "STANDBY"]
    for s in states:
        vw.set_state(s)
        if s == "SPEAKING":
            vw.set_speech_volume(0.6)
        if s == "LISTENING":
            vw.set_audio_spectrum([0.5, 0.7, 0.4, 0.6, 0.3, 0.5, 0.2, 0.4])
        # Settle eased values at their state targets (the 40-tick loop runs
        # in real milliseconds, so easing would render half-blended states)
        animated = s in ("WAKE", "PROCESSING", "SPEAKING")
        vw._color_cur = QVector3D(_COLORS[s])
        vw._halo_cur = QVector3D(_HALO_COLORS[s])
        vw._color2_cur = QVector3D(_COLORS2[s])
        vw._scan_cur = 0.3 if s == "PROCESSING" else (0.1 if animated else 0.0)
        vw._glitch_cur = 0.08 if s == "PROCESSING" else 0.0
        vw._fresnel_cur = 2.0 if animated else 3.0
        vw._echo_cur = 1.0 if s == "PROCESSING" else 0.0
        vw._rings.clear()
        vw._time += 2.7  # advance animation phase, expire leftovers
        app.processEvents()
        frame = vw.grabFramebuffer()
        # Compose over dark gray so the transparent overlay reads like on a desktop
        out = QImage(frame.size(), QImage.Format.Format_RGB32)
        out.fill(QColor(28, 28, 32))
        p = QPainter(out)
        p.drawImage(0, 0, frame)
        p.end()
        out.save(f"preview_{s.lower()}.png")
        print(f"preview_{s.lower()}.png saved")

    vw.close()


if __name__ == "__main__":
    main()
