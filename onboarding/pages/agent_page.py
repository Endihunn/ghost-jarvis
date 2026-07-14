"""Agent detection + manual configuration + connection test page."""
import logging
from PyQt6.QtWidgets import (
    QWizardPage, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QMessageBox, QGroupBox, QFormLayout,
    QProgressBar, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer

from onboarding.detector import scan_for_agents
from ghost_bridge import _http_is_alive

logger = logging.getLogger("onboarding")


class AgentPage(QWizardPage):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTitle("Configurar Agente")
        self.setSubTitle("Detecta automáticamente tu agente o introduce los datos manualmente.")

        self._detected: list[dict] = []
        self._selected_agent: dict | None = None

        layout = QVBoxLayout(self)

        # --- Auto-detect section ---
        detect_group = QGroupBox("Detección automática")
        detect_layout = QVBoxLayout(detect_group)

        self._detect_combo = QComboBox()
        self._detect_combo.setEnabled(False)
        self._detect_combo.currentIndexChanged.connect(self._on_agent_selected)

        self._detect_btn = QPushButton("Buscar agentes instalados")
        self._detect_btn.clicked.connect(self._do_detect)

        detect_layout.addWidget(self._detect_btn)
        detect_layout.addWidget(self._detect_combo)

        self._detect_info = QLabel("")
        self._detect_info.setWordWrap(True)
        detect_layout.addWidget(self._detect_info)

        layout.addWidget(detect_group)

        # --- Manual section ---
        manual_group = QGroupBox("Configuración manual")
        manual_form = QFormLayout()

        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("ws://127.0.0.1:18789")
        manual_form.addRow("Gateway URL:", self._url_edit)

        self._token_edit = QLineEdit()
        self._token_edit.setPlaceholderText("Token de la puerta de enlace")
        self._token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        manual_form.addRow("Token:", self._token_edit)

        self._session_edit = QLineEdit()
        self._session_edit.setPlaceholderText("ej. agent:main:default")
        manual_form.addRow("Session key:", self._session_edit)

        manual_group.setLayout(manual_form)
        layout.addWidget(manual_group)

        # --- Test connection ---
        test_layout = QHBoxLayout()
        self._test_btn = QPushButton("Probar conexión")
        self._test_btn.clicked.connect(self._do_test)
        self._test_result = QLabel("")
        test_layout.addWidget(self._test_btn)
        test_layout.addWidget(self._test_result)
        layout.addLayout(test_layout)

        layout.addStretch()

        # Run auto-detect once on show
        QTimer.singleShot(0, self._do_detect)

    def _do_detect(self):
        self._detect_btn.setEnabled(False)
        self._detect_btn.setText("Buscando...")
        self._detected = scan_for_agents()
        self._detect_combo.clear()

        if not self._detected:
            self._detect_combo.addItem("No se detectó ningún agente")
            self._detect_combo.setEnabled(False)
            self._detect_info.setText(
                "No encontramos OpenClaw ni Kimi en las rutas conocidas. "
                "Introduce los datos manualmente abajo."
            )
        else:
            self._detect_combo.addItem("Selecciona un agente detectado...")
            for agent in self._detected:
                self._detect_combo.addItem(agent["name"], agent)
            self._detect_combo.setEnabled(True)
            self._detect_info.setText(
                f"Se detectaron {len(self._detected)} agente(s). "
                "Elige uno de la lista o introduce los datos manualmente."
            )

        self._detect_btn.setEnabled(True)
        self._detect_btn.setText("Buscar agentes instalados")
        self.completeChanged.emit()

    def _on_agent_selected(self, index: int):
        if index <= 0:
            self._selected_agent = None
            return
        agent = self._detect_combo.itemData(index)
        if not agent:
            return
        self._selected_agent = agent
        self._url_edit.setText(agent.get("gateway_url", ""))
        self._token_edit.setText(agent.get("gateway_token", ""))
        self.completeChanged.emit()

    def _do_test(self):
        url = self._url_edit.text().strip()
        token = self._token_edit.text().strip()
        if not url or not token:
            QMessageBox.warning(self, "Faltan datos", "Introduce URL y token antes de probar.")
            return

        self._test_result.setText("⏳ Probando...")
        self._test_btn.setEnabled(False)

        # Temporarily override config for the test
        from config import APP_CONFIG
        old_url = APP_CONFIG.gateway_url
        old_token = APP_CONFIG.gateway_token
        APP_CONFIG.gateway_url = url
        APP_CONFIG.gateway_token = token

        try:
            ok = _http_is_alive(timeout=5.0)
        except Exception as e:
            logger.debug("Connection test error: %s", e)
            ok = False
        finally:
            APP_CONFIG.gateway_url = old_url
            APP_CONFIG.gateway_token = old_token

        self._test_btn.setEnabled(True)
        if ok:
            self._test_result.setText("✅ Conexión OK")
        else:
            self._test_result.setText("❌ No se pudo conectar")

    def isComplete(self) -> bool:
        return bool(self._url_edit.text().strip() and self._token_edit.text().strip())

    def get_config(self) -> dict:
        return {
            "gateway_url": self._url_edit.text().strip(),
            "gateway_token": self._token_edit.text().strip(),
            "session_key": self._session_edit.text().strip(),
        }
