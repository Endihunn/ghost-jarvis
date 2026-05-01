"""Configuration dialog for Ghost Jarvis.

Six-tab layout: Connection, Wake Words, Personality (SOUL),
Audio Advanced, GPU, Voice & Visuals.
"""
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QDialogButtonBox,
    QLabel, QWidget, QPushButton, QHBoxLayout, QMessageBox,
    QTabWidget, QTextEdit, QSpinBox, QDoubleSpinBox, QCheckBox,
    QComboBox, QSlider, QGroupBox
)
from PyQt6.QtCore import Qt

from config import APP_CONFIG


class ConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ghost Jarvis — Configuración")
        self.setMinimumWidth(520)
        self.setMinimumHeight(480)
        self.setWindowFlags(Qt.WindowType.Dialog)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        header = QLabel(
            "<b>Configuración de Ghost Jarvis</b><br>"
            "Personaliza conexión, audio, GPU, voz y visuales."
        )
        header.setTextFormat(Qt.TextFormat.RichText)
        header.setWordWrap(True)
        layout.addWidget(header)

        self._tabs = QTabWidget(self)
        self._tabs.addTab(self._build_tab_connection(), "Conexión")
        self._tabs.addTab(self._build_tab_wake(), "Wake Words")
        self._tabs.addTab(self._build_tab_audio(), "Audio Avanzado")
        self._tabs.addTab(self._build_tab_gpu(), "GPU")
        self._tabs.addTab(self._build_tab_voice_visual(), "Voz y Visuales")
        layout.addWidget(self._tabs)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_save)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ------------------------------------------------------------------ Tab: Connection
    def _build_tab_connection(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)
        form = QFormLayout()
        form.setSpacing(10)

        self._gateway_url = QLineEdit()
        self._gateway_url.setPlaceholderText("ws://127.0.0.1:18789")
        form.addRow("Gateway URL (WebSocket):", self._gateway_url)

        self._gateway_token = QLineEdit()
        self._gateway_token.setPlaceholderText("Token de la puerta de enlace")
        self._gateway_token.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Token de la puerta de enlace:", self._gateway_token)

        self._session_key = QLineEdit()
        self._session_key.setPlaceholderText("ej. agent:main:main")
        form.addRow("Clave de sesión predeterminada:", self._session_key)

        vbox.addLayout(form)
        load_btn = QPushButton("Cargar desde openclaw.json")
        load_btn.setToolTip("Lee la URL y token del archivo de config de OpenClaw")
        load_btn.clicked.connect(self._load_from_openclaw)
        vbox.addWidget(load_btn)

        self._privacy_mode = QCheckBox("Modo privado (no loguear transcripciones ni respuestas)")
        self._privacy_mode.setToolTip("Oculta todo el contenido de conversación de los logs")
        vbox.addWidget(self._privacy_mode)
        vbox.addStretch()
        return tab

    # ------------------------------------------------------------------ Tab: Wake Words
    def _build_tab_wake(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        hint = QLabel(
            "Escribe una frase de despertar por línea. Ghost escuchará cualquiera de ellas.<br>"
            "<i>Incluye 'jarvis' para estilo J.A.R.V.I.S.</i>"
        )
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setWordWrap(True)
        vbox.addWidget(hint)

        self._wake_edit = QTextEdit()
        self._wake_edit.setPlaceholderText("oye ghost\ney ghost\nghost\njarvis")
        vbox.addWidget(self._wake_edit)
        return tab

    # ------------------------------------------------------------------ Tab: Audio Advanced
    def _build_tab_audio(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        # VAD
        vad_group = QGroupBox("Detección de Voz (VAD)")
        vad_form = QFormLayout()
        self._vad_agg = QSpinBox()
        self._vad_agg.setRange(0, 3)
        self._vad_agg.setToolTip("0=permisivo, 3=agresivo")
        vad_form.addRow("Agresividad VAD:", self._vad_agg)

        self._silence_ms = QSpinBox()
        self._silence_ms.setRange(200, 2000)
        self._silence_ms.setSuffix(" ms")
        vad_form.addRow("Timeout de silencio:", self._silence_ms)

        self._min_utt_ms = QSpinBox()
        self._min_utt_ms.setRange(100, 1000)
        self._min_utt_ms.setSuffix(" ms")
        vad_form.addRow("Mín. duración utterance:", self._min_utt_ms)

        self._ring_ms = QSpinBox()
        self._ring_ms.setRange(200, 1500)
        self._ring_ms.setSuffix(" ms")
        vad_form.addRow("Buffer previo (ring):", self._ring_ms)
        vad_group.setLayout(vad_form)
        vbox.addWidget(vad_group)

        # Mic
        mic_group = QGroupBox("Micrófono")
        mic_form = QFormLayout()
        self._auto_gain = QCheckBox("Auto-gain")
        mic_form.addRow(self._auto_gain)

        self._mic_gain = QDoubleSpinBox()
        self._mic_gain.setRange(0.5, 8.0)
        self._mic_gain.setSingleStep(0.1)
        mic_form.addRow("Ganancia manual:", self._mic_gain)
        mic_group.setLayout(mic_form)
        vbox.addWidget(mic_group)

        vbox.addStretch()
        return tab

    # ------------------------------------------------------------------ Tab: GPU
    def _build_tab_gpu(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        self._gpu_enabled = QCheckBox("Usar GPU (CUDA) para Whisper")
        self._gpu_enabled.setToolTip("Requiere reiniciar la app para aplicar")
        vbox.addWidget(self._gpu_enabled)

        self._gpu_info = QLabel("")
        self._gpu_info.setWordWrap(True)
        vbox.addWidget(self._gpu_info)

        vbox.addStretch()
        return tab

    # ------------------------------------------------------------------ Tab: Voice & Visuals
    def _build_tab_voice_visual(self) -> QWidget:
        tab = QWidget()
        vbox = QVBoxLayout(tab)

        # Voice effects
        voice_group = QGroupBox("Efectos de Voz J.A.R.V.I.S.")
        voice_form = QFormLayout()

        self._jarvis_enabled = QCheckBox("Activar efectos J.A.R.V.I.S.")
        voice_form.addRow(self._jarvis_enabled)

        self._jarvis_reverb = QDoubleSpinBox()
        self._jarvis_reverb.setRange(0.0, 1.0)
        self._jarvis_reverb.setSingleStep(0.05)
        self._jarvis_reverb.setDecimals(2)
        voice_form.addRow("Reverb:", self._jarvis_reverb)

        self._jarvis_delay = QDoubleSpinBox()
        self._jarvis_delay.setRange(0.0, 1.0)
        self._jarvis_delay.setSingleStep(0.05)
        self._jarvis_delay.setDecimals(2)
        voice_form.addRow("Delay:", self._jarvis_delay)

        self._jarvis_pitch = QSpinBox()
        self._jarvis_pitch.setRange(-6, 6)
        self._jarvis_pitch.setSuffix(" semitonos")
        voice_form.addRow("Pitch shift:", self._jarvis_pitch)

        self._jarvis_compressor = QCheckBox("Compresión tipo radio")
        voice_form.addRow(self._jarvis_compressor)
        voice_group.setLayout(voice_form)
        vbox.addWidget(voice_group)

        # Visuals
        vis_group = QGroupBox("Visuales")
        vis_form = QFormLayout()

        self._vis_fps = QSpinBox()
        self._vis_fps.setRange(30, 144)
        self._vis_fps.setSuffix(" FPS")
        vis_form.addRow("Target FPS:", self._vis_fps)

        self._vis_quality = QComboBox()
        self._vis_quality.addItems(["low", "medium", "high", "ultra"])
        vis_form.addRow("Calidad:", self._vis_quality)

        self._particles = QCheckBox("Partículas orbitales")
        vis_form.addRow(self._particles)

        self._scanlines = QCheckBox("Scanlines holográficas")
        vis_form.addRow(self._scanlines)

        self._grid = QCheckBox("Grid de suelo")
        vis_form.addRow(self._grid)

        self._wireframe = QCheckBox("Wireframe de cubos")
        vis_form.addRow(self._wireframe)

        self._glitch = QCheckBox("Efecto glitch (procesando)")
        vis_form.addRow(self._glitch)

        vis_group.setLayout(vis_form)
        vbox.addWidget(vis_group)

        vbox.addStretch()
        return tab

    # ------------------------------------------------------------------ Load values
    def _load_values(self):
        # Connection
        self._gateway_url.setText(APP_CONFIG.gateway_url or "")
        self._gateway_token.setText(APP_CONFIG.gateway_token or "")
        self._session_key.setText(APP_CONFIG.session_key or "")
        self._privacy_mode.setChecked(APP_CONFIG.privacy_mode)

        # Wake words
        self._wake_edit.setPlainText("\n".join(APP_CONFIG.wake_phrases or []))

        # Audio advanced
        self._vad_agg.setValue(APP_CONFIG.vad_aggressiveness)
        self._silence_ms.setValue(APP_CONFIG.silence_timeout_ms)
        self._min_utt_ms.setValue(APP_CONFIG.min_utterance_ms)
        self._ring_ms.setValue(APP_CONFIG.ring_buffer_ms)
        self._auto_gain.setChecked(APP_CONFIG.mic_auto_gain)
        self._mic_gain.setValue(APP_CONFIG.mic_gain)

        # GPU
        self._gpu_enabled.setChecked(APP_CONFIG.gpu_enabled)
        try:
            from gpu_utils import get_gpu_info
            info = get_gpu_info()
            self._gpu_info.setText(
                f"CUDA disponible: {info['cuda_available']}<br>"
                f"GPU: {info['device_name']}<br>"
                f"CUDA version: {info['cuda_version']}"
            )
        except Exception as e:
            self._gpu_info.setText(f"No se pudo detectar GPU: {e}")

        # Voice & Visuals
        self._jarvis_enabled.setChecked(APP_CONFIG.jarvis_voice_effects)
        self._jarvis_reverb.setValue(APP_CONFIG.jarvis_reverb)
        self._jarvis_delay.setValue(APP_CONFIG.jarvis_delay)
        self._jarvis_pitch.setValue(APP_CONFIG.jarvis_pitch_shift)
        self._jarvis_compressor.setChecked(APP_CONFIG.jarvis_compressor)
        self._vis_fps.setValue(APP_CONFIG.visual_fps)
        self._vis_quality.setCurrentText(APP_CONFIG.visual_quality)
        self._particles.setChecked(APP_CONFIG.particles_enabled)
        self._scanlines.setChecked(APP_CONFIG.scanlines_enabled)
        self._grid.setChecked(APP_CONFIG.grid_enabled)
        self._wireframe.setChecked(APP_CONFIG.wireframe_enabled)
        self._glitch.setChecked(APP_CONFIG.glitch_enabled)

    # ------------------------------------------------------------------ Helpers
    def _load_from_openclaw(self):
        try:
            import json
            path = Path(APP_CONFIG.openclaw_config)
            data = json.loads(path.read_text(encoding="utf-8"))
            gw = data.get("gateway", {})
            port = gw.get("port", 18789)
            bind = gw.get("bind", "loopback")
            host = "127.0.0.1" if bind in ("loopback", None) else "0.0.0.0"
            self._gateway_url.setText(f"ws://{host}:{port}")
            auth = gw.get("auth", {})
            if auth.get("mode") == "token":
                self._gateway_token.setText(auth.get("token", ""))
            QMessageBox.information(self, "Cargado", "Valores leídos de openclaw.json")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo leer openclaw.json:\n{e}")

    # ------------------------------------------------------------------ Save
    def _on_save(self):
        # Connection
        APP_CONFIG.gateway_url = self._gateway_url.text().strip()
        APP_CONFIG.gateway_token = self._gateway_token.text().strip()
        APP_CONFIG.session_key = self._session_key.text().strip()
        APP_CONFIG.privacy_mode = self._privacy_mode.isChecked()

        # Wake words
        raw = self._wake_edit.toPlainText()
        phrases = [ln.strip().lower() for ln in raw.splitlines() if ln.strip()]
        APP_CONFIG.wake_phrases = phrases

        # Audio advanced
        APP_CONFIG.vad_aggressiveness = self._vad_agg.value()
        APP_CONFIG.silence_timeout_ms = self._silence_ms.value()
        APP_CONFIG.min_utterance_ms = self._min_utt_ms.value()
        APP_CONFIG.ring_buffer_ms = self._ring_ms.value()
        APP_CONFIG.mic_auto_gain = self._auto_gain.isChecked()
        APP_CONFIG.mic_gain = self._mic_gain.value()

        # GPU
        APP_CONFIG.gpu_enabled = self._gpu_enabled.isChecked()

        # Voice & Visuals
        APP_CONFIG.jarvis_voice_effects = self._jarvis_enabled.isChecked()
        APP_CONFIG.jarvis_reverb = self._jarvis_reverb.value()
        APP_CONFIG.jarvis_delay = self._jarvis_delay.value()
        APP_CONFIG.jarvis_pitch_shift = self._jarvis_pitch.value()
        APP_CONFIG.jarvis_compressor = self._jarvis_compressor.isChecked()
        APP_CONFIG.visual_fps = self._vis_fps.value()
        APP_CONFIG.visual_quality = self._vis_quality.currentText()
        APP_CONFIG.particles_enabled = self._particles.isChecked()
        APP_CONFIG.scanlines_enabled = self._scanlines.isChecked()
        APP_CONFIG.grid_enabled = self._grid.isChecked()
        APP_CONFIG.wireframe_enabled = self._wireframe.isChecked()
        APP_CONFIG.glitch_enabled = self._glitch.isChecked()

        APP_CONFIG.save()
        self.accept()
