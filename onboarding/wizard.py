"""Ghost Jarvis Onboarding QWizard."""
import logging
from PyQt6.QtWidgets import QWizard

from config import APP_CONFIG
from onboarding.pages import WelcomePage, AgentPage, AudioPage, FinishPage
from startup_installer import install_startup, remove_startup

logger = logging.getLogger("onboarding")


class OnboardingWizard(QWizard):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Ghost Jarvis — Configuración inicial")
        self.setMinimumSize(640, 520)

        self._welcome = WelcomePage()
        self._agent = AgentPage()
        self._audio = AudioPage()
        self._finish = FinishPage()

        self.addPage(self._welcome)
        self.addPage(self._agent)
        self.addPage(self._audio)
        self.addPage(self._finish)

        self.currentIdChanged.connect(self._on_page_changed)

    def _on_page_changed(self, page_id: int):
        if self.page(page_id) is self._finish:
            agent_cfg = self._agent.get_config()
            summary = (
                f"<b>Agente:</b> {agent_cfg.get('gateway_url', 'N/A')}<br>"
                f"<b>Session key:</b> {agent_cfg.get('session_key', 'auto')}<br><br>"
                f"Haz clic en <b>Finalizar</b> para guardar la configuración "
                f"y empezar a usar Ghost Jarvis."
            )
            self._finish.set_summary(summary)

    def done(self, result):
        if result == QWizard.DialogCode.Accepted:
            self._apply_config()
        super().done(result)

    def _apply_config(self):
        try:
            agent_cfg = self._agent.get_config()
            audio_cfg = self._audio.get_config()

            if agent_cfg.get("gateway_url"):
                APP_CONFIG.gateway_url = agent_cfg["gateway_url"]
            if agent_cfg.get("gateway_token"):
                APP_CONFIG.gateway_token = agent_cfg["gateway_token"]
            if agent_cfg.get("session_key"):
                APP_CONFIG.session_key = agent_cfg["session_key"]
            else:
                import uuid
                APP_CONFIG.session_key = f"agent:main:{uuid.uuid4().hex[:8]}"

            APP_CONFIG.mic_auto_gain = audio_cfg.get("mic_auto_gain", True)
            APP_CONFIG.mic_gain = audio_cfg.get("mic_gain", 2.5)

            APP_CONFIG.save()
            logger.info("Onboarding config saved.")

            # Handle startup preference
            autostart = self._finish.autostart()
            if autostart:
                install_startup()
            else:
                remove_startup()
        except Exception as e:
            logger.error("Failed to apply onboarding config: %s", e)
