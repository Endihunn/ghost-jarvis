"""Final summary page for the onboarding wizard."""
from PyQt6.QtWidgets import QWizardPage, QVBoxLayout, QLabel, QCheckBox
from PyQt6.QtCore import Qt


class FinishPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Todo listo")
        self.setSubTitle("Ghost Jarvis está configurado y listo para usar.")

        layout = QVBoxLayout(self)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._summary)

        self._autostart = QCheckBox("Iniciar Ghost Jarvis con el sistema")
        self._autostart.setChecked(True)
        layout.addWidget(self._autostart)

        layout.addStretch()

    def set_summary(self, text: str):
        self._summary.setText(text)

    def isComplete(self) -> bool:
        return True

    def autostart(self) -> bool:
        return self._autostart.isChecked()
