"""Calibration wizard for Ghost Jarvis voice detection.

4-step interactive wizard:
  1. Noise floor  — 5 s silence to measure ambient noise level
  2. Mic level    — speak normally; recommends optimal mic_gain
  3. Wake word    — live STT test to verify wake phrase detection
  4. Apply        — preview and save recommended settings
"""
import collections
import logging
import numpy as np

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from config import APP_CONFIG
from audio_engine import _check_wake

logger = logging.getLogger("calib")

_SS = """
QDialog, QWidget   { background:#0d1117; color:#c9d1d9; font-size:13px; }
QLabel             { color:#c9d1d9; }
QLabel#step_title  { font-size:15px; font-weight:bold; color:#58a6ff; }
QLabel#hint        { color:#8b949e; font-size:11px; }
QLabel#ok          { color:#3fb950; font-weight:bold; }
QLabel#bad         { color:#f85149; font-weight:bold; }
QLabel#warn        { color:#d29922; font-weight:bold; }
QPushButton {
    background:#21262d; color:#c9d1d9; border:1px solid #30363d;
    padding:6px 20px; border-radius:6px; min-width:80px;
}
QPushButton:hover     { background:#30363d; }
QPushButton:disabled  { color:#555; background:#161b22; border-color:#222; }
QPushButton#primary   { background:#1f6feb; color:white; border:none; }
QPushButton#primary:hover    { background:#388bfd; }
QPushButton#primary:disabled { background:#1f3a6e; color:#666; }
QGroupBox {
    border:1px solid #30363d; border-radius:6px; margin-top:10px; padding:8px;
    color:#8b949e; font-size:11px;
}
QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px; }
QDoubleSpinBox, QSpinBox {
    background:#161b22; color:#c9d1d9; border:1px solid #30363d;
    border-radius:4px; padding:3px 6px;
}
"""


# ---------------------------------------------------------------------------
# VU bar widget
# ---------------------------------------------------------------------------

class _VUBar(QProgressBar):
    _GREEN  = "#2ea043"
    _YELLOW = "#d29922"
    _BLUE   = "#1f6feb"
    _RED    = "#f85149"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(False)
        self.setFixedHeight(26)
        self._thr = 30
        self._peak = 0

    def set_threshold_pct(self, pct: int):
        self._thr = max(1, pct)

    def set_vol(self, vol: float):
        pct = min(int(vol * 100), 100)
        if pct > self._peak:
            self._peak = pct
        self.setValue(pct)
        if pct < self._thr:
            color = self._GREEN
        elif pct < 55:
            color = self._YELLOW
        elif pct < 80:
            color = self._BLUE
        else:
            color = self._RED
        self.setStyleSheet(
            f"QProgressBar {{ background:#21262d; border:1px solid #30363d; "
            f"border-radius:4px; height:26px; }}"
            f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}"
        )

    def reset_peak(self):
        self._peak = 0


# ---------------------------------------------------------------------------
# Step pages
# ---------------------------------------------------------------------------

def _make_label(text: str, obj_name: str = "", word_wrap: bool = True) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(word_wrap)
    if obj_name:
        lbl.setObjectName(obj_name)
    return lbl


def _sep() -> QWidget:
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet("background:#30363d;")
    return line


# ---------------------------------------------------------------------------
# CalibrationDialog
# ---------------------------------------------------------------------------

class CalibrationDialog(QDialog):

    _MEASURE_SECS = 5      # duration of each measurement phase
    _SAMPLE_RATE_HZ = 33   # approx volume_changed frequency
    _TARGET_VOICE_PEAK = 0.45

    def __init__(self, audio_engine, parent=None):
        super().__init__(parent)
        self._audio = audio_engine
        self.setWindowTitle("Ghost Jarvis — Calibración de voz")
        self.setMinimumWidth(540)
        self.setStyleSheet(_SS)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint
        )

        # ── measurement state ────────────────────────────────────────────────
        self._vol_buf: collections.deque = collections.deque(
            maxlen=self._MEASURE_SECS * self._SAMPLE_RATE_HZ + 30
        )
        self._measuring = False
        self._countdown = 0
        self._noise_floor = 0.0
        self._voice_peak = 0.0
        self._last_text = ""
        self._last_score = 0
        self._last_matched = False

        # ── recommendations (filled during wizard) ───────────────────────────
        self._rec_gain = APP_CONFIG.mic_gain
        self._rec_energy_thr = 0.30
        self._rec_vad_agg = APP_CONFIG.vad_aggressiveness

        # ── timers ───────────────────────────────────────────────────────────
        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._on_countdown_tick)

        self._vu_timer = QTimer(self)
        self._vu_timer.timeout.connect(self._flush_vu)
        self._vu_timer.start(40)   # 25 Hz display refresh
        self._pending_vol: float = 0.0

        self._build_ui()
        self._audio.volume_changed.connect(self._on_vol)
        self._audio.utterance_detected.connect(self._on_utterance)
        self._go_step(0)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # progress dots
        self._dot_label = QLabel()
        self._dot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot_label.setObjectName("hint")
        root.addWidget(self._dot_label)

        # VU bar (shared across all steps)
        vu_group = QGroupBox("Nivel de micrófono (en tiempo real)")
        vu_vbox = QVBoxLayout(vu_group)
        self._vu = _VUBar()
        vu_vbox.addWidget(self._vu)
        row = QHBoxLayout()
        self._vu_pct_lbl = QLabel("0%")
        self._vu_pct_lbl.setObjectName("hint")
        self._vu_state_lbl = QLabel("—")
        row.addWidget(self._vu_pct_lbl)
        row.addStretch()
        row.addWidget(self._vu_state_lbl)
        vu_vbox.addLayout(row)
        root.addWidget(vu_group)

        root.addWidget(_sep())

        # step pages
        self._stack = QStackedWidget()
        self._pages = [
            self._build_step0(),
            self._build_step1(),
            self._build_step2(),
            self._build_step3(),
        ]
        for p in self._pages:
            self._stack.addWidget(p)
        root.addWidget(self._stack, stretch=1)

        root.addWidget(_sep())

        # nav buttons
        nav = QHBoxLayout()
        self._btn_back = QPushButton("← Atrás")
        self._btn_back.clicked.connect(self._go_prev)
        nav.addWidget(self._btn_back)
        nav.addStretch()
        self._btn_next = QPushButton("Siguiente →")
        self._btn_next.setObjectName("primary")
        self._btn_next.clicked.connect(self._go_next)
        nav.addWidget(self._btn_next)
        root.addLayout(nav)

    # ── Step 0: noise floor ───────────────────────────────────────────────────

    def _build_step0(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.addWidget(_make_label("Paso 1 · Silencio de fondo", "step_title"))
        vbox.addWidget(_make_label(
            "Haz silencio total durante 5 segundos. El asistente medirá el "
            "nivel de ruido ambiente (ventiladores, música de fondo, HVAC) "
            "para ajustar los umbrales correctamente."
        ))
        self._noise_countdown = QLabel("Listo para medir.")
        self._noise_countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._noise_countdown)

        self._noise_bar = QProgressBar()
        self._noise_bar.setRange(0, self._MEASURE_SECS)
        self._noise_bar.setValue(0)
        self._noise_bar.setTextVisible(False)
        self._noise_bar.setFixedHeight(10)
        self._noise_bar.setStyleSheet(
            "QProgressBar { background:#21262d; border-radius:4px; }"
            "QProgressBar::chunk { background:#3fb950; border-radius:4px; }"
        )
        vbox.addWidget(self._noise_bar)

        self._noise_result = QLabel("")
        self._noise_result.setObjectName("hint")
        vbox.addWidget(self._noise_result)

        btn = QPushButton("▶ Iniciar medición de silencio")
        btn.clicked.connect(self._start_noise_measure)
        vbox.addWidget(btn)
        vbox.addStretch()
        return w

    # ── Step 1: mic level ─────────────────────────────────────────────────────

    def _build_step1(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.addWidget(_make_label("Paso 2 · Nivel de voz", "step_title"))
        vbox.addWidget(_make_label(
            "Habla en tono normal durante 5 segundos, como si le dieras una "
            'instruccion a Ghost. Di algo como "oye ghost, que hora es".'
        ))

        self._voice_countdown = QLabel("Listo para medir.")
        self._voice_countdown.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(self._voice_countdown)

        self._voice_bar = QProgressBar()
        self._voice_bar.setRange(0, self._MEASURE_SECS)
        self._voice_bar.setValue(0)
        self._voice_bar.setTextVisible(False)
        self._voice_bar.setFixedHeight(10)
        self._voice_bar.setStyleSheet(
            "QProgressBar { background:#21262d; border-radius:4px; }"
            "QProgressBar::chunk { background:#1f6feb; border-radius:4px; }"
        )
        vbox.addWidget(self._voice_bar)

        self._voice_result = QLabel("")
        self._voice_result.setObjectName("hint")
        vbox.addWidget(self._voice_result)

        self._gain_rec = QLabel("")
        vbox.addWidget(self._gain_rec)

        btn = QPushButton("▶ Iniciar medición de voz")
        btn.clicked.connect(self._start_voice_measure)
        vbox.addWidget(btn)
        vbox.addStretch()
        return w

    # ── Step 2: wake word test ────────────────────────────────────────────────

    def _build_step2(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.addWidget(_make_label("Paso 3 · Prueba de wake word", "step_title"))

        phrases_str = ", ".join(f'"{p}"' for p in APP_CONFIG.wake_phrases[:4])
        vbox.addWidget(_make_label(
            f"Di en voz alta cualquiera de tus frases de activación: {phrases_str}. "
            "El sistema mostrará qué transcribió Whisper y qué puntaje obtuvo la coincidencia."
        ))

        result_box = QGroupBox("Último resultado STT")
        rb_vbox = QVBoxLayout(result_box)
        self._wake_text_lbl = QLabel("(esperando...)")
        self._wake_text_lbl.setObjectName("hint")
        rb_vbox.addWidget(self._wake_text_lbl)

        score_row = QHBoxLayout()
        score_row.addWidget(QLabel("Puntaje de coincidencia:"))
        self._wake_score_lbl = QLabel("—")
        self._wake_score_lbl.setObjectName("hint")
        score_row.addWidget(self._wake_score_lbl)
        score_row.addStretch()
        self._wake_status_lbl = QLabel("")
        score_row.addWidget(self._wake_status_lbl)
        rb_vbox.addLayout(score_row)

        thr_row = QHBoxLayout()
        thr_row.addWidget(_make_label(f"Umbral actual: {APP_CONFIG.wake_fuzz_threshold}", "hint"))
        self._wake_thr_hint = QLabel("")
        thr_row.addWidget(self._wake_thr_hint)
        rb_vbox.addLayout(thr_row)

        vbox.addWidget(result_box)
        vbox.addStretch()
        return w

    # ── Step 3: apply ─────────────────────────────────────────────────────────

    def _build_step3(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.addWidget(_make_label("Paso 4 · Aplicar calibración", "step_title"))
        vbox.addWidget(_make_label(
            "Revisa los valores recomendados. Puedes ajustarlos antes de guardar. "
            "Los cambios se aplican en tiempo real y se guardan en config.json."
        ))

        form_box = QGroupBox("Valores recomendados")
        form = QFormLayout(form_box)

        self._apply_gain = QDoubleSpinBox()
        self._apply_gain.setRange(0.5, 8.0)
        self._apply_gain.setSingleStep(0.1)
        self._apply_gain.setDecimals(2)
        self._apply_gain.setValue(APP_CONFIG.mic_gain)
        form.addRow("Ganancia de micrófono:", self._apply_gain)

        self._apply_vad = QSpinBox()
        self._apply_vad.setRange(0, 3)
        self._apply_vad.setValue(APP_CONFIG.vad_aggressiveness)
        self._apply_vad.setToolTip("0=permisivo (más sensible), 3=agresivo (más estricto)")
        form.addRow("Agresividad VAD (0–3):", self._apply_vad)

        self._apply_silence = QSpinBox()
        self._apply_silence.setRange(200, 2000)
        self._apply_silence.setSuffix(" ms")
        self._apply_silence.setValue(APP_CONFIG.silence_timeout_ms)
        form.addRow("Timeout de silencio:", self._apply_silence)

        self._apply_fuzz = QSpinBox()
        self._apply_fuzz.setRange(50, 100)
        self._apply_fuzz.setValue(APP_CONFIG.wake_fuzz_threshold)
        self._apply_fuzz.setToolTip(
            "Puntaje mínimo para reconocer la wake word. "
            "Baja si no te detecta; sube si hay falsos positivos."
        )
        form.addRow("Umbral de wake word (50–100):", self._apply_fuzz)

        vbox.addWidget(form_box)

        self._apply_summary = QLabel("")
        self._apply_summary.setWordWrap(True)
        self._apply_summary.setObjectName("hint")
        vbox.addWidget(self._apply_summary)

        self._btn_apply = QPushButton("✔ Aplicar y guardar")
        self._btn_apply.setObjectName("primary")
        self._btn_apply.clicked.connect(self._do_apply)
        vbox.addWidget(self._btn_apply)

        self._apply_done = QLabel("")
        self._apply_done.setObjectName("ok")
        vbox.addWidget(self._apply_done)

        vbox.addStretch()
        return w

    # ── navigation ────────────────────────────────────────────────────────────

    def _go_step(self, step: int):
        self._stack.setCurrentIndex(step)
        self._step = step

        dots = ""
        for i in range(4):
            dots += "●  " if i == step else "○  "
        self._dot_label.setText(dots.strip())

        self._btn_back.setEnabled(step > 0)
        if step == 3:
            self._btn_next.setText("Cerrar")
        else:
            self._btn_next.setText("Siguiente →")

        # pre-fill apply page when entering it
        if step == 3:
            self._apply_gain.setValue(self._rec_gain)
            self._apply_vad.setValue(self._rec_vad_agg)
            summary_parts = []
            if abs(self._rec_gain - APP_CONFIG.mic_gain) > 0.09:
                summary_parts.append(
                    f"• Ganancia: {APP_CONFIG.mic_gain:.1f} → {self._rec_gain:.1f}"
                )
            if self._rec_vad_agg != APP_CONFIG.vad_aggressiveness:
                summary_parts.append(
                    f"• Agresividad VAD: {APP_CONFIG.vad_aggressiveness} → {self._rec_vad_agg}"
                )
            if not summary_parts:
                summary_parts.append("La configuración actual parece correcta.")
            self._apply_summary.setText("\n".join(summary_parts))
            self._apply_done.setText("")

    def _go_prev(self):
        if self._step > 0:
            self._stop_measuring()
            self._go_step(self._step - 1)

    def _go_next(self):
        if self._step == 3:
            self.accept()
        elif self._step < 3:
            self._stop_measuring()
            self._go_step(self._step + 1)

    # ── audio slots ───────────────────────────────────────────────────────────

    @pyqtSlot(float)
    def _on_vol(self, vol: float):
        self._pending_vol = vol
        if self._measuring:
            self._vol_buf.append(vol)

    def _flush_vu(self):
        vol = self._pending_vol
        self._vu.set_vol(vol)
        pct = min(int(vol * 100), 100)
        self._vu_pct_lbl.setText(f"{pct}%")
        thr_pct = int(self._rec_energy_thr * 100)
        if pct < thr_pct:
            self._vu_state_lbl.setText("silencio")
            self._vu_state_lbl.setObjectName("hint")
        else:
            self._vu_state_lbl.setText("VOZ DETECTADA")
            self._vu_state_lbl.setObjectName("ok")
        self._vu_state_lbl.setStyleSheet("")  # force re-evaluate objectName style

    @pyqtSlot(str, bool, str)
    def _on_utterance(self, text: str, is_long: bool = False, lang: str = "?"):
        if self._step != 2:
            return
        self._last_text = text
        has_wake, _ = _check_wake(text)

        # re-run to get score (ugly but avoids touching audio_engine internals)
        from rapidfuzz import fuzz
        text_lower = text.lower().strip()
        text_words = text_lower.split()
        best_score = 0
        best_wp = ""
        for wp in APP_CONFIG.wake_phrases:
            if " " not in wp:
                score = max((fuzz.ratio(wp, w) for w in text_words), default=0)
            else:
                score = fuzz.partial_ratio(wp, text_lower)
            if score > best_score:
                best_score = score
                best_wp = wp

        self._last_score = best_score
        self._last_matched = has_wake

        self._wake_text_lbl.setText(f'STT detectó: "{text}"')
        self._wake_score_lbl.setText(f'{best_score} / {APP_CONFIG.wake_fuzz_threshold}  (frase: "{best_wp}")')

        if has_wake:
            self._wake_status_lbl.setText("✔ DETECTADA")
            self._wake_status_lbl.setObjectName("ok")
            self._wake_thr_hint.setText("")
        else:
            self._wake_status_lbl.setText("✗ NO detectada")
            self._wake_status_lbl.setObjectName("bad")
            if best_score >= APP_CONFIG.wake_fuzz_threshold - 15:
                self._wake_thr_hint.setText(
                    f"Cerca del umbral. Considera bajar el umbral a {best_score - 3}."
                )
                self._wake_thr_hint.setObjectName("warn")
            else:
                self._wake_thr_hint.setText(
                    "Puntaje muy bajo — revisa el micrófono o las frases de activación."
                )
                self._wake_thr_hint.setObjectName("bad")
        self._wake_status_lbl.setStyleSheet("")
        self._wake_thr_hint.setStyleSheet("")

    # ── measurement logic ─────────────────────────────────────────────────────

    def _stop_measuring(self):
        self._measuring = False
        self._countdown_timer.stop()

    def _start_noise_measure(self):
        self._vol_buf.clear()
        self._noise_bar.setValue(0)
        self._noise_countdown.setText("Midiendo…")
        self._noise_result.setText("")
        self._measuring = True
        self._countdown = self._MEASURE_SECS
        self._countdown_timer.start(1000)

    def _start_voice_measure(self):
        self._vol_buf.clear()
        self._vu.reset_peak()
        self._voice_bar.setValue(0)
        self._voice_countdown.setText("Midiendo…")
        self._voice_result.setText("")
        self._gain_rec.setText("")
        self._measuring = True
        self._countdown = self._MEASURE_SECS
        self._countdown_timer.start(1000)

    def _on_countdown_tick(self):
        self._countdown -= 1
        elapsed = self._MEASURE_SECS - self._countdown

        if self._step == 0:
            self._noise_bar.setValue(elapsed)
            self._noise_countdown.setText(
                f"Midiendo silencio… {self._countdown}s restantes"
                if self._countdown > 0 else "Listo."
            )
        elif self._step == 1:
            self._voice_bar.setValue(elapsed)
            self._voice_countdown.setText(
                f"Midiendo voz… {self._countdown}s restantes"
                if self._countdown > 0 else "Listo."
            )

        if self._countdown <= 0:
            self._countdown_timer.stop()
            self._measuring = False
            if self._step == 0:
                self._finish_noise()
            elif self._step == 1:
                self._finish_voice()

    def _finish_noise(self):
        if not self._vol_buf:
            self._noise_result.setText("Sin datos — ¿micrófono disponible?")
            return
        samples = list(self._vol_buf)
        p95 = float(np.percentile(samples, 95))
        self._noise_floor = p95

        # Energy VAD threshold = 3× the 95th percentile noise (with min 0.20)
        self._rec_energy_thr = max(p95 * 3.0, 0.20)
        self._rec_energy_thr = min(self._rec_energy_thr, 0.55)
        self._vu.set_threshold_pct(int(self._rec_energy_thr * 100))

        quality = "Bajo (bueno)" if p95 < 0.04 else ("Moderado" if p95 < 0.12 else "Alto — considera reducir ruido ambiental")
        self._noise_result.setText(
            f"Piso de ruido (P95): {p95:.3f}  — {quality}\n"
            f"Umbral de energía recomendado: {self._rec_energy_thr:.2f}"
        )
        self._btn_next.setEnabled(True)

    def _finish_voice(self):
        if not self._vol_buf:
            self._voice_result.setText("Sin datos — ¿micrófono disponible?")
            return
        samples = list(self._vol_buf)
        p80 = float(np.percentile(samples, 80))
        peak = float(np.max(samples))
        self._voice_peak = peak

        # Recommend gain so that P80 voice level lands near target
        if p80 > 0.01:
            scale = self._TARGET_VOICE_PEAK / p80
            self._rec_gain = float(np.clip(APP_CONFIG.mic_gain * scale, 0.5, 8.0))
        else:
            self._rec_gain = APP_CONFIG.mic_gain

        # Recommend VAD aggressiveness based on noise floor vs voice peak ratio
        snr = peak / max(self._noise_floor, 0.001)
        if snr > 10:
            self._rec_vad_agg = 3
        elif snr > 5:
            self._rec_vad_agg = 2
        elif snr > 2:
            self._rec_vad_agg = 1
        else:
            self._rec_vad_agg = 0

        rating = "Excelente" if snr > 10 else ("Buena" if snr > 5 else ("Marginal" if snr > 2 else "Pobre — hay mucho ruido"))
        self._voice_result.setText(
            f"Nivel de voz (P80): {p80:.3f}  Pico: {peak:.3f}  SNR: {snr:.1f}× — {rating}"
        )

        if abs(self._rec_gain - APP_CONFIG.mic_gain) > 0.09:
            self._gain_rec.setText(
                f"Ganancia recomendada: {self._rec_gain:.1f}  (actual: {APP_CONFIG.mic_gain:.1f})"
            )
            self._gain_rec.setObjectName("warn")
        else:
            self._gain_rec.setText("Ganancia actual: correcta.")
            self._gain_rec.setObjectName("ok")
        self._gain_rec.setStyleSheet("")

    # ── apply ─────────────────────────────────────────────────────────────────

    def _do_apply(self):
        APP_CONFIG.mic_gain = self._apply_gain.value()
        APP_CONFIG.vad_aggressiveness = self._apply_vad.value()
        APP_CONFIG.silence_timeout_ms = self._apply_silence.value()
        APP_CONFIG.wake_fuzz_threshold = self._apply_fuzz.value()
        APP_CONFIG.save()

        # Live-update AudioEngine without restart where possible
        try:
            self._audio._input_gain = APP_CONFIG.mic_gain
            self._audio.vad.set_mode(APP_CONFIG.vad_aggressiveness)
        except Exception as e:
            logger.warning("Could not hot-apply to AudioEngine: %s", e)

        self._apply_done.setText("✔ Configuración guardada.")
        self._btn_apply.setEnabled(False)
        logger.info(
            "Calibration applied: gain=%.2f vad=%d silence=%dms fuzz=%d",
            APP_CONFIG.mic_gain, APP_CONFIG.vad_aggressiveness,
            APP_CONFIG.silence_timeout_ms, APP_CONFIG.wake_fuzz_threshold,
        )

    # ── cleanup ───────────────────────────────────────────────────────────────

    def _disconnect(self):
        self._stop_measuring()
        self._vu_timer.stop()
        try:
            self._audio.volume_changed.disconnect(self._on_vol)
        except Exception:
            pass
        try:
            self._audio.utterance_detected.disconnect(self._on_utterance)
        except Exception:
            pass

    def closeEvent(self, event):
        self._disconnect()
        super().closeEvent(event)

    def reject(self):
        self._disconnect()
        super().reject()

    def accept(self):
        self._disconnect()
        super().accept()
