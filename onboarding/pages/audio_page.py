"""Audio calibration page (gain, auto-gain, wake word info)."""
from PyQt6.QtWidgets import (
    QWizardPage, QVBoxLayout, QLabel, QCheckBox,
    QSlider, QHBoxLayout, QGroupBox, QFormLayout,
)
from PyQt6.QtCore import Qt


class AudioPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Calibrar Audio")
        self.setSubTitle("Ajusta el micrófono y las frases de despertar.")

        layout = QVBoxLayout(self)

        info = QLabel(
            "Ghost Jarvis escucha continuamente. Ajusta la ganancia para que "
            "tu voz se detecte claramente sin capturar ruido de fondo.<br><br>"
            "Puedes cambiar estos ajustes más tarde desde Configuración."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        # Mic settings
        mic_group = QGroupBox("Micrófono")
        mic_form = QFormLayout()

        self._auto_gain = QCheckBox("Activar auto-gain")
        self._auto_gain.setChecked(True)
        self._auto_gain.stateChanged.connect(self._on_auto_changed)
        mic_form.addRow(self._auto_gain)

        gain_layout = QHBoxLayout()
        self._gain_slider = QSlider(Qt.Orientation.Horizontal)
        self._gain_slider.setRange(5, 80)
        self._gain_slider.setValue(25)
        self._gain_label = QLabel("2.5x")
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        gain_layout.addWidget(self._gain_slider)
        gain_layout.addWidget(self._gain_label)
        mic_form.addRow("Ganancia manual:", gain_layout)

        mic_group.setLayout(mic_form)
        layout.addWidget(mic_group)

        # Wake phrases
        wake_group = QGroupBox("Frases de despertar (wake words)")
        wake_layout = QVBoxLayout(wake_group)
        wake_info = QLabel(
            "Por defecto Ghost responde a:<br>"
            "<i>oye ghost, ey ghost, ghost, jarvis</i><br><br>"
            "Puedes personalizarlas desde Configuración después del setup."
        )
        wake_info.setTextFormat(Qt.TextFormat.RichText)
        wake_info.setWordWrap(True)
        wake_layout.addWidget(wake_info)
        layout.addWidget(wake_group)

        layout.addStretch()

    def _on_auto_changed(self, state):
        self._gain_slider.setEnabled(state == Qt.CheckState.Unchecked.value)

    def _on_gain_changed(self, value: int):
        self._gain_label.setText(f"{value / 10.0:.1f}x")

    def isComplete(self) -> bool:
        return True

    def get_config(self) -> dict:
        return {
            "mic_auto_gain": self._auto_gain.isChecked(),
            "mic_gain": self._gain_slider.value() / 10.0,
        }
