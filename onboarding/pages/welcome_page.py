"""Welcome / intro page for the onboarding wizard."""
from PyQt6.QtWidgets import QWizardPage, QVBoxLayout, QLabel, QCheckBox
from PyQt6.QtCore import Qt


class WelcomePage(QWizardPage):
    def __init__(self, agent_name: str = "Ghost", parent=None):
        super().__init__(parent)
        self._agent_name = agent_name
        self.setTitle(f"Bienvenido a {agent_name} Jarvis")
        self.setSubTitle("Tu interfaz de voz tipo J.A.R.V.I.S.")

        layout = QVBoxLayout(self)

        info = QLabel(
            f"{agent_name} Jarvis escucha continuamente y te permite interactuar "
            "con tu agente AI mediante comandos de voz.<br><br>"
            "Vamos a configurar tu agente y calibrar el audio en unos pocos pasos."
        )
        info.setWordWrap(True)
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        self._autostart = QCheckBox(f"Iniciar {agent_name} Jarvis con el sistema")
        self._autostart.setChecked(True)
        layout.addWidget(self._autostart)

        layout.addStretch()

    def isComplete(self) -> bool:
        return True

    def autostart(self) -> bool:
        return self._autostart.isChecked()
