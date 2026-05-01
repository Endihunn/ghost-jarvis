"""Ghost Jarvis — Main application entry point.

Transparent, frameless, centered overlay with OpenGL visuals.
Optimizado para latencia mínima y conversación fluida.
"""
import sys
import random
import logging
from pathlib import Path

# --- Single-instance guard (cross-platform) ---
_gh_lock_fd = None
try:
    if sys.platform == "win32":
        import win32event
        import win32api
        import winerror
        _gh_mutex = win32event.CreateMutex(None, False, "GhostJarvis_SingleInstance_Mutex")
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            print("[Ghost Jarvis] Ya hay una instancia corriendo. Saliendo.")
            sys.exit(0)
    else:
        import fcntl
        from platformdirs import user_runtime_dir
        lock_dir = Path(user_runtime_dir("GhostJarvis", "GhostLabs"))
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / "ghost-jarvis.lock"
        _gh_lock_fd = open(lock_file, "w")
        fcntl.lockf(_gh_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except Exception:
    print("[Ghost Jarvis] Ya hay una instancia corriendo. Saliendo.")
    sys.exit(0)

import log_config
log_config.setup_logging()

from PyQt6.QtCore import Qt, QTimer, pyqtSlot, QRect
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor
from PyQt6.QtWidgets import (
    QApplication, QDialog, QMainWindow, QSystemTrayIcon, QMenu, QMessageBox, QStyle
)
from PyQt6.QtGui import QSurfaceFormat

from state_machine import StateMachine, State
from visual_gl import VisualGLWidget
from audio_engine import AudioEngine, _check_wake
from ghost_bridge import GhostBridge, StandbyChecker
from startup_installer import install_startup, remove_startup
from config import APP_CONFIG
from config_dialog import ConfigDialog
from voice_effects import get_jarvis_wake_responses
from voice_log_window import QtLogHandler, VoiceLogWindow
from system_volume import save_and_duck, restore_volume

WAKE_RESPONSES = get_jarvis_wake_responses()

OVERLAY_WIDTH = APP_CONFIG.overlay_width
OVERLAY_HEIGHT = APP_CONFIG.overlay_height


class GhostJarvisApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ghost Jarvis")
        self._position_overlay()
        self._drag_pos = self.pos()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAutoFillBackground(False)

        # Visual widget (OpenGL)
        self.visual = VisualGLWidget(self)
        self.setCentralWidget(self.visual)

        # Right-click context menu on the overlay and the GL widget
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.visual.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.visual.customContextMenuRequested.connect(self._on_context_menu)

        # State machine
        self.sm = StateMachine()
        self._setup_state_machine()

        # Audio
        self.audio = AudioEngine()
        self.audio.wake_detected.connect(self._on_wake)
        self.audio.utterance_detected.connect(self._on_utterance)
        self.audio.volume_changed.connect(self._on_listening_volume)
        self.audio.spectrum_changed.connect(self._on_audio_spectrum)

        # Ghost bridge
        self.ghost = GhostBridge()

        # Voice log window
        self._log_handler = QtLogHandler()
        logging.getLogger().addHandler(self._log_handler)
        self._voice_log = VoiceLogWindow(self._log_handler)

        # Tray icon
        self.tray = QSystemTrayIcon(self)
        self._setup_tray()
        self._update_tray_status(self.sm.state)

        # Speech volume fake-animation timer
        self._speech_vol_timer = QTimer(self)
        self._speech_vol_timer.timeout.connect(self._update_speech_volume)
        self._speech_vol_timer.start(50)

        # Idle safety timer
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._check_idle_timeout)
        self._idle_timer.start(1000)
        self._last_activity = 0

        self._listen_timer = None
        self._speech_end_timer = None
        self._pending_utterance = ""

        # Pre-load whisper in background (200 ms after init so the UI shows first)
        QTimer.singleShot(200, self._preload_whisper)

        # Standby health-check timer (only pings while in STANDBY)
        self._standby_timer = QTimer(self)
        self._standby_timer.timeout.connect(self._check_standby)
        self._standby_timer.start(10000)

        # Start audio
        self.audio.start()

        # Pre-generate edge-tts MP3 for all wake responses in background
        self.audio.prewarm_tts(WAKE_RESPONSES)

        # Always start in STANDBY; check agent availability in background (non-blocking)
        self.sm.transition(State.STANDBY)
        QTimer.singleShot(0, self._check_standby)

    def _preload_whisper(self):
        import threading
        threading.Thread(target=self._do_preload_whisper, daemon=True, name="whisper-preload").start()

    def _do_preload_whisper(self):
        try:
            from audio_engine import _load_whisper
            _load_whisper()
        except Exception as e:
            logging.getLogger("whisper").warning("Preload error: %s", e)

    def _setup_state_machine(self):
        @self.sm.on_enter(State.STANDBY)
        def _(ctx):
            self.visual.set_state("STANDBY", "Stand-by")
            self.audio.play_sound("listen_off")
            self.audio.resume_input()
            if APP_CONFIG.duck_volume_enabled:
                restore_volume()

        @self.sm.on_enter(State.IDLE)
        def _(ctx):
            self.visual.set_state("IDLE", "En espera")
            self.audio.play_sound("listen_off")
            self.audio.resume_input()
            if APP_CONFIG.duck_volume_enabled:
                restore_volume()

        @self.sm.on_enter(State.WAKE)
        def _(ctx):
            if APP_CONFIG.duck_volume_enabled:
                save_and_duck(APP_CONFIG.duck_volume_level)
            self.visual.set_state("WAKE", "Despertando...")
            self.audio.play_sound("listen_on")
            self.audio.pause_input()
            self.audio.stop_speaking()
            resp = random.choice(WAKE_RESPONSES)
            self.audio.speak_wake(resp, blocking=False)

            _polls = [0]

            def _wait_wake_done():
                _polls[0] += 1
                # Hard timeout: proceed after 5s even if TTS is still busy
                if not self.audio.is_speaking() or _polls[0] > 83:
                    if _polls[0] > 83:
                        logging.getLogger("state").warning("WAKE TTS timeout, forcing LISTENING")
                    self.audio.resume_input()
                    self.sm.transition(State.LISTENING)
                else:
                    QTimer.singleShot(60, _wait_wake_done)

            QTimer.singleShot(120, _wait_wake_done)

        @self.sm.on_enter(State.LISTENING)
        def _(ctx):
            if APP_CONFIG.duck_volume_enabled:
                save_and_duck(APP_CONFIG.duck_volume_level)
            self.visual.set_state("LISTENING", "Escuchando...")
            self.audio.play_sound("ready")
            self.audio.resume_input()
            self._listen_timeout = 0
            self._listen_timer = QTimer(self)
            self._listen_timer.timeout.connect(self._on_listen_tick)
            self._listen_timer.start(1000)
            # Process any utterance buffered during WAKE
            if self._pending_utterance:
                pending = self._pending_utterance
                self._pending_utterance = ""
                self.sm.context.user_prompt = pending
                QTimer.singleShot(200, lambda: self.sm.transition(State.PROCESSING))

        @self.sm.on_enter(State.PROCESSING)
        def _(ctx):
            self.visual.set_state("PROCESSING", "Pensando...")
            self.audio.pause_input()
            if self._listen_timer and self._listen_timer.isActive():
                self._listen_timer.stop()
            prompt = ctx.user_prompt
            if not prompt:
                self.sm.transition(State.IDLE)
                return
            self.ghost.send(
                prompt,
                on_response=self._on_ghost_response,
                on_error=self._on_ghost_error,
            )

        @self.sm.on_enter(State.SPEAKING)
        def _(ctx):
            if APP_CONFIG.duck_volume_enabled:
                save_and_duck(APP_CONFIG.duck_volume_level)
            text = ctx.ghost_response
            self.visual.set_state("SPEAKING", "Hablando...")
            self.audio.pause_input()
            try:
                self.audio.speech_finished.disconnect(self._on_speech_finished)
            except Exception:
                pass
            self.audio.speech_finished.connect(self._on_speech_finished)
            self.audio.speak_agent(text, blocking=False)
            self._speech_end_timer = QTimer(self)
            self._speech_end_timer.timeout.connect(self._check_speech_end)
            self._speech_end_timer.start(500)

        self.sm.on_transition(lambda old, new: print(f"[STATE] {old.name} -> {new.name}"))
        self.sm.on_transition(lambda old, new: self._update_tray_status(new))

    def _update_tray_status(self, state):
        status_map = {
            State.STANDBY: "Stand-by (offline)",
            State.IDLE: "En espera",
            State.WAKE: "Despertando...",
            State.LISTENING: "Escuchando...",
            State.PROCESSING: "Pensando...",
            State.SPEAKING: "Hablando...",
        }
        label = status_map.get(state, state.name)
        self.tray.setToolTip(f"Ghost Jarvis — {label}")

    def _build_tray_menu(self) -> QMenu:
        """Build the context menu shared by the tray icon and the overlay."""
        menu = QMenu()

        act_show = QAction("Mostrar", self)
        act_show.triggered.connect(self.show)
        menu.addAction(act_show)

        act_hide = QAction("Ocultar", self)
        act_hide.triggered.connect(self.hide)
        menu.addAction(act_hide)

        menu.addSeparator()

        act_config = QAction("Configuración", self)
        act_config.triggered.connect(self._show_config_dialog)
        menu.addAction(act_config)

        act_calib = QAction("Calibrar detección de voz", self)
        act_calib.triggered.connect(self._show_calibration_dialog)
        menu.addAction(act_calib)

        act_voice_log = QAction("Log de captura de voz", self)
        act_voice_log.triggered.connect(self._toggle_voice_log)
        menu.addAction(act_voice_log)

        act_startup = QAction("Iniciar con Windows", self)
        act_startup.setCheckable(True)
        lnk = (
            Path.home()
            / "AppData"
            / "Roaming"
            / "Microsoft"
            / "Windows"
            / "Start Menu"
            / "Programs"
            / "Startup"
            / "Ghost Jarvis.lnk"
        )
        act_startup.setChecked(lnk.exists())
        act_startup.triggered.connect(
            lambda checked: install_startup() if checked else remove_startup()
        )
        menu.addAction(act_startup)

        menu.addSeparator()
        act_quit = QAction("Salir", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        return menu

    def _setup_tray(self):
        icon_path = Path(__file__).with_name("assets") / "icon.ico"
        if icon_path.exists():
            self.tray.setIcon(QIcon(str(icon_path)))
            self.setWindowIcon(QIcon(str(icon_path)))
        else:
            px = QPixmap(64, 64)
            px.fill(Qt.GlobalColor.transparent)
            p = QPainter(px)
            p.setBrush(QColor("#00f5d4"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(8, 8, 48, 48, 12, 12)
            p.end()
            self.tray.setIcon(QIcon(px))

        self.tray.setContextMenu(self._build_tray_menu())
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _on_context_menu(self, pos):
        """Show the same tray menu on right-click over the overlay."""
        sender = self.sender()
        if sender is None:
            sender = self
        menu = self._build_tray_menu()
        global_pos = sender.mapToGlobal(pos)
        menu.exec(global_pos)

    def _toggle_voice_log(self):
        QTimer.singleShot(0, self._do_toggle_voice_log)

    def _do_toggle_voice_log(self):
        if self._voice_log.isVisible():
            self._voice_log.hide()
        else:
            self._voice_log.show()
            self._voice_log.raise_()

    def _show_config_dialog(self):
        # Defer to next event-loop tick so the tray menu closes before the modal blocks
        QTimer.singleShot(0, self._do_show_config_dialog)

    def _do_show_config_dialog(self):
        from config_dialog import ConfigDialog
        if hasattr(self, '_config_dlg') and self._config_dlg is not None:
            self._config_dlg.raise_()
            self._config_dlg.activateWindow()
            return
        dlg = ConfigDialog(self)
        self._config_dlg = dlg
        dlg.finished.connect(lambda: setattr(self, '_config_dlg', None))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            logging.getLogger("config").info("Config updated.")
            if self.sm.state == State.STANDBY:
                self._check_standby()

    def _show_calibration_dialog(self):
        QTimer.singleShot(0, self._do_show_calibration_dialog)

    def _do_show_calibration_dialog(self):
        from calibration_dialog import CalibrationDialog
        dlg = CalibrationDialog(self.audio, parent=None)
        dlg.exec()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()

    def _on_wake(self):
        # Wake word detected: interrupt any ongoing speech and transition
        if self.sm.state == State.IDLE:
            self.sm.transition(State.WAKE)
        elif self.sm.state == State.SPEAKING:
            # Interrupt speaking and start listening again
            self.audio.stop_speaking()
            if self._speech_end_timer and self._speech_end_timer.isActive():
                self._speech_end_timer.stop()
            try:
                self.audio.speech_finished.disconnect(self._on_speech_finished)
            except Exception:
                pass
            self.sm.transition(State.WAKE)
        elif self.sm.state == State.LISTENING:
            # Already listening, reset timeout
            self._listen_timeout = 0
        elif self.sm.state == State.PROCESSING:
            self.ghost.cancel_current()
            self.sm.transition(State.WAKE)

    def _on_utterance(self, text: str, is_long: bool = False, lang: str = "?"):
        state = self.sm.state
        # Long utterances (>5s) are usually background audio (TV, music).
        # However, if the user explicitly said the wake word inside a long clip
        # (e.g. because VAD didn't cut promptly), we still want to react.
        if is_long and state in (State.IDLE, State.STANDBY, State.SPEAKING, State.WAKE, State.PROCESSING):
            has_wake, clean_text = _check_wake(text)
            if not has_wake:
                if not APP_CONFIG.privacy_mode:
                    logging.getLogger("state").debug("Ignored long utterance in %s: %s", state.name, text[:60])
                return
            if not APP_CONFIG.privacy_mode:
                logging.getLogger("state").info("Long utterance contains wake word, allowing: %s", text[:60])
            is_long = False
            text = clean_text if clean_text else text
        if state == State.STANDBY:
            has_wake, _ = _check_wake(text)
            if has_wake:
                self.audio.speak_local("Ghost en espera. El agente aún no está disponible.", blocking=False)
            else:
                if not APP_CONFIG.privacy_mode:
                    logging.getLogger("state").debug("STANDBY ignore: %s", text)
            return
        if state == State.LISTENING:
            self.sm.context.user_prompt = text
            self.sm.transition(State.PROCESSING)
        elif state == State.WAKE:
            self._pending_utterance = text
            if not APP_CONFIG.privacy_mode:
                logging.getLogger("state").debug("Buffered utterance during WAKE: %s", text)
        elif state == State.SPEAKING:
            # Allow wake word to interrupt ongoing speech
            has_wake, _ = _check_wake(text)
            if has_wake:
                self._on_wake()
        elif state == State.PROCESSING:
            # Wake word during processing cancels the in-flight Ghost request
            # and starts a new turn. Other speech is ignored (we cannot send
            # a second prompt while one is in-flight).
            has_wake, _ = _check_wake(text)
            if has_wake:
                logging.getLogger("state").debug("Wake during PROCESSING — cancelling current request")
                self.ghost.cancel_current()
                self._on_wake()
        elif state == State.IDLE:
            has_wake, clean_text = _check_wake(text)
            if has_wake:
                if clean_text:
                    self._pending_utterance = clean_text
                self._on_wake()
            else:
                if not APP_CONFIG.privacy_mode:
                    logging.getLogger("state").debug("IDLE ignore (no wake word): %s", text)

    def _on_listening_volume(self, vol: float):
        self.visual.set_audio_volume(vol)
        if vol > 0.05:
            self._last_activity = 0

    def _on_audio_spectrum(self, bins: list):
        self.visual.set_audio_spectrum(bins)

    def _on_listen_tick(self):
        self._listen_timeout += 1
        self._last_activity = 0
        if self._listen_timeout >= 10:
            self._listen_timer.stop()
            self.sm.transition(State.IDLE)

    def _on_ghost_response(self, text: str, is_question: bool):
        if not APP_CONFIG.privacy_mode:
            logging.getLogger("ghost").debug("Response: %s | question=%s", text[:200], is_question)
        self.sm.context.ghost_response = text
        self.sm.context.session_active = is_question
        self._last_activity = 0
        self.sm.transition(State.SPEAKING)

    def _on_ghost_error(self, error: str):
        logging.getLogger("ghost").error("Ghost error: %s", error)
        # If the agent is unreachable, switch to STANDBY instead of speaking the raw error
        if "no encontrado" in error.lower() or "config" in error.lower() or "timeout" in error.lower() or "no respondió" in error.lower():
            self.sm.transition(State.STANDBY)
            self._standby_timer.start(30000)
            return
        self.sm.context.ghost_response = f"Error al contactar a Ghost: {error}"
        self.sm.transition(State.SPEAKING)

    def _update_speech_volume(self):
        if self.audio.is_speaking():
            import math, time
            v = 0.3 + 0.4 * abs(math.sin(time.time() * 8))
            self.visual.set_speech_volume(v)
        else:
            self.visual.set_speech_volume(0.0)

    def _on_speech_finished(self):
        self._check_speech_end()

    def _check_speech_end(self):
        if not self.audio.is_speaking():
            self._speech_end_timer.stop()
            try:
                self.audio.speech_finished.disconnect(self._on_speech_finished)
            except Exception:
                pass
            if self.sm.context.session_active:
                self.sm.transition(State.LISTENING)
            else:
                self.sm.transition(State.IDLE)

    def _check_standby(self):
        if self.sm.state == State.STANDBY:
            checker = StandbyChecker(timeout=5.0, parent=self)
            checker.available.connect(self._on_agent_connected)
            checker.finished.connect(checker.deleteLater)
            checker.start()

    def _on_agent_connected(self):
        self.audio.speak_local("Ghost conectado.", blocking=False)
        self.sm.transition(State.IDLE)
        self._standby_timer.stop()

    def _check_idle_timeout(self):
        self._last_activity += 1
        # Only LISTENING and WAKE have a no-activity timeout. PROCESSING owns its
        # own deadline (GhostWorker, 240s) and SPEAKING ends via _check_speech_end.
        if self.sm.state in (State.LISTENING, State.WAKE) and self._last_activity > 60:
            self.audio.stop_speaking()
            self.sm.transition(State.IDLE)

    def _position_overlay(self):
        """Center the overlay on the primary screen."""
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
        else:
            geo = QRect(0, 0, 1920, 1080)
        x = geo.x() + (geo.width() - OVERLAY_WIDTH) // 2
        y = geo.y() + (geo.height() - OVERLAY_HEIGHT) // 2
        self.setGeometry(x, y, OVERLAY_WIDTH, OVERLAY_HEIGHT)

    def mousePressEvent(self, event):
        self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(
                self.pos() + event.globalPosition().toPoint() - self._drag_pos
            )
            self._drag_pos = event.globalPosition().toPoint()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            "Ghost Jarvis",
            "La app sigue corriendo en la bandeja. Haz doble clic para mostrarla.",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )

    def _quit(self):
        # Stop all Qt timers so callbacks don't fire during teardown
        for timer in (
            self._speech_vol_timer, self._idle_timer, self._standby_timer,
            self._listen_timer, self._speech_end_timer,
        ):
            if timer is not None and timer.isActive():
                timer.stop()
        # Tear down GL first, while the widget's context is still valid.
        try:
            self.visual._cleanup_gl()
        except Exception:
            pass
        # Always restore system volume on exit so we don't leave it ducked
        if APP_CONFIG.duck_volume_enabled:
            restore_volume()
        # Detach the Qt log handler before any Qt teardown so background
        # threads can't fire log events into deleted slots/signals. Drop the
        # last Python reference so logging.shutdown() (atexit) skips it.
        try:
            logging.getLogger().removeHandler(self._log_handler)
            self._log_handler.close()
        except Exception:
            pass
        self._log_handler = None
        self.ghost.close()
        self.audio.stop()
        self.tray.hide()
        self._voice_log.close()
        QApplication.instance().quit()


def _has_valid_config() -> bool:
    """Return True if the user has already configured a gateway and token."""
    return bool(
        APP_CONFIG.gateway_url.strip()
        and APP_CONFIG.gateway_token.strip()
        and APP_CONFIG.session_key.strip()
    )


def main():
    fmt = QSurfaceFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyle("Fusion")

    if not _has_valid_config():
        from onboarding.wizard import OnboardingWizard
        wizard = OnboardingWizard()
        if wizard.exec() != QWizard.DialogCode.Accepted:
            sys.exit(0)

    window = GhostJarvisApp()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
