"""Ghost Jarvis — Main application entry point.

Transparent, frameless, centered overlay with OpenGL visuals.
Optimizado para latencia mínima y conversación fluida.
"""
import sys
import math
import random
import logging
import time as time_mod
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
    QApplication, QDialog, QMainWindow, QSystemTrayIcon, QMenu, QMessageBox, QStyle, QWizard
)
from PyQt6.QtGui import QSurfaceFormat

from rapidfuzz import fuzz

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

# --- Local voice commands (handled without a round-trip to the agent) ---
# Matched against the full post-wake utterance (lowercased, ≤4 words).
_STOP_CMDS = {
    "para", "párale", "parale", "cállate", "callate", "silencio", "basta",
    "stop", "detente", "alto", "cancela", "cancelar", "olvídalo", "olvidalo",
    "déjalo", "dejalo", "nada", "ya estuvo", "nada más", "nada mas",
}
_REPEAT_CMDS = {
    "repite", "repítelo", "repitelo", "repite eso", "otra vez",
    "qué dijiste", "que dijiste", "repite por favor",
}
_VOL_UP_CMDS = {
    "más alto", "mas alto", "más fuerte", "mas fuerte", "sube el volumen",
    "sube volumen", "habla más alto", "habla mas alto",
}
_VOL_DOWN_CMDS = {
    "más bajo", "mas bajo", "baja el volumen", "baja volumen",
    "más quedito", "mas quedito", "habla más bajo", "habla mas bajo",
}


def _is_stop_command(text: str) -> bool:
    t = text.strip().lower()
    return t in _STOP_CMDS and len(t.split()) <= 2


class GhostJarvisApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_CONFIG.agent_name} Jarvis")
        self._position_overlay()
        self._drag_pos = self.pos()
        self._dragged = False
        # "Modo mover": suspende el click-through para poder arrastrar el
        # overlay desde el menú de la bandeja.
        self._move_mode = False
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
        # Read-aloud monitor: queue of texts from other sessions (webchat) to
        # speak when idle. All access happens on the GUI thread.
        self._foreign_queue: list[str] = []
        self.ghost.foreign_response.connect(self._on_foreign_response)

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
        # True while the current SPEAKING turn is fed by streamed sentences
        # (sentence_ready) instead of a single speak_agent(full_text) call.
        self._streaming_active = False
        # Last full agent reply, for the local "repite" command.
        self._last_response = ""

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

        # Read-aloud monitor: subscribe to the webchat session and speak its
        # replies aloud. Never monitor our own session (would double-read).
        if APP_CONFIG.read_all_responses:
            mon = (APP_CONFIG.monitor_session or "").strip()
            own_tail = APP_CONFIG.session_key.split(":")[-1] if APP_CONFIG.session_key else ""
            if mon and mon != own_tail and mon != APP_CONFIG.session_key:
                self.ghost.start_monitor([mon])

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
            self.audio.set_listen_mode(False)
            if APP_CONFIG.duck_volume_enabled:
                restore_volume()

        @self.sm.on_enter(State.IDLE)
        def _(ctx):
            self.visual.set_state("IDLE", "En espera")
            self.audio.play_sound("listen_off")
            self.audio.resume_input()
            self.audio.set_listen_mode(False)
            if APP_CONFIG.duck_volume_enabled:
                restore_volume()
            # If a webchat reply is queued, read it now that we're free.
            QTimer.singleShot(300, self._flush_foreign_queue)

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
            self.audio.set_listen_mode(True)
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
            # Barge-in: keep the mic open so the wake word can cancel the
            # in-flight run (the _on_utterance PROCESSING branch). Without
            # barge-in this used to pause input, which made that branch
            # unreachable dead code.
            if APP_CONFIG.barge_in_enabled:
                self.audio.resume_input()
            else:
                self.audio.pause_input()
            self.audio.set_listen_mode(False)
            if self._listen_timer and self._listen_timer.isActive():
                self._listen_timer.stop()
            prompt = ctx.user_prompt
            if not prompt:
                self.sm.transition(State.IDLE)
                return
            self._streaming_active = False
            self.ghost.send(
                prompt,
                on_response=self._on_ghost_response,
                on_error=self._on_ghost_error,
                on_sentence=self._on_ghost_sentence,
            )

        @self.sm.on_enter(State.SPEAKING)
        def _(ctx):
            if APP_CONFIG.duck_volume_enabled:
                save_and_duck(APP_CONFIG.duck_volume_level)
            self.visual.set_state("SPEAKING", "Hablando...")
            # Barge-in: mic stays open while we speak; _on_utterance discards
            # echoes of our own TTS and reacts to the wake word / "cállate".
            if APP_CONFIG.barge_in_enabled:
                self.audio.resume_input()
            else:
                self.audio.pause_input()
            try:
                self.audio.speech_finished.disconnect(self._on_speech_finished)
            except Exception:
                pass
            self.audio.speech_finished.connect(self._on_speech_finished)
            if not self._streaming_active:
                # Non-streamed turn (kill-switch off, "repite", foreign reply,
                # or error message): speak the full text in one call.
                self.audio.speak_agent(ctx.ghost_response, blocking=False)
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
        self.tray.setToolTip(f"{APP_CONFIG.agent_name} Jarvis — {label}")

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

        act_move = QAction("Modo mover (arrastrar overlay)", self)
        act_move.setCheckable(True)
        act_move.setChecked(self._move_mode)
        act_move.toggled.connect(self._set_move_mode)
        menu.addAction(act_move)

        act_center = QAction("Centrar overlay", self)
        act_center.triggered.connect(self._center_overlay)
        menu.addAction(act_center)

        size_menu = menu.addMenu("Tamaño del overlay")
        for label, px in (("Compacto (260)", 260), ("Normal (320)", 320), ("Grande (420)", 420)):
            act_size = QAction(label, self)
            act_size.setCheckable(True)
            act_size.setChecked(int(APP_CONFIG.overlay_width) == px)
            act_size.triggered.connect(lambda _=False, p=px: self._set_overlay_size(p))
            size_menu.addAction(act_size)

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

        self._tray_menu = self._build_tray_menu()
        self.tray.setContextMenu(self._tray_menu)
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
            self._apply_click_through(self._effective_click_through())
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
            self._end_speaking_turn()
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
                self.audio.speak_local(f"{APP_CONFIG.agent_name} en espera. El agente aún no está disponible.", blocking=False)
            else:
                if not APP_CONFIG.privacy_mode:
                    logging.getLogger("state").debug("STANDBY ignore: %s", text)
            return
        if state == State.LISTENING:
            if APP_CONFIG.local_commands_enabled and self._handle_local_command(text):
                return
            self.sm.context.user_prompt = text
            self.sm.transition(State.PROCESSING)
        elif state == State.WAKE:
            self._pending_utterance = text
            if not APP_CONFIG.privacy_mode:
                logging.getLogger("state").debug("Buffered utterance during WAKE: %s", text)
        elif state == State.SPEAKING:
            # Barge-in: the mic is open while we talk, so the first job is to
            # discard transcriptions of our own TTS coming back through it.
            if self._is_tts_echo(text):
                return
            has_wake, clean = _check_wake(text)
            if not has_wake:
                return
            if clean and _is_stop_command(clean):
                # "ghost cállate" — cut the voice and go idle, no new turn.
                logging.getLogger("state").info("Voice stop command during SPEAKING")
                self._end_speaking_turn()
                self.sm.transition(State.IDLE)
                return
            if clean:
                self._pending_utterance = clean
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

    def _end_speaking_turn(self):
        """Stop TTS + the end-of-speech timer and detach the finished signal.
        Used by voice interruptions (wake word / stop command) during SPEAKING."""
        self.audio.stop_speaking()
        self._streaming_active = False
        if self._speech_end_timer and self._speech_end_timer.isActive():
            self._speech_end_timer.stop()
        try:
            self.audio.speech_finished.disconnect(self._on_speech_finished)
        except Exception:
            pass

    def _is_tts_echo(self, text: str) -> bool:
        """True if `text` looks like the mic picking up our own TTS voice.

        Compares against the sentence the speaker is playing right now (plus
        the previous one — Whisper lags the audio by a second or two). Also
        guards the case where the agent's reply contains the word "ghost",
        which would otherwise self-trigger the wake check.
        """
        now_playing = self.audio.get_now_playing()
        if len(now_playing) < 8:
            return False
        score = fuzz.partial_ratio(text.lower(), now_playing.lower())
        if score >= 75:
            if not APP_CONFIG.privacy_mode:
                logging.getLogger("state").debug("Discarded TTS echo (%d%%): %s", score, text[:60])
            return True
        return False

    def _handle_local_command(self, text: str) -> bool:
        """Intercept short utility commands in LISTENING without an agent trip.
        Returns True if the utterance was consumed."""
        t = text.strip().lower()
        if len(t.split()) > 4:
            return False
        log = logging.getLogger("state")

        if _is_stop_command(t):
            log.info("Local command: stop/cancel")
            if self._listen_timer and self._listen_timer.isActive():
                self._listen_timer.stop()
            self.sm.transition(State.IDLE)
            return True

        if t in _REPEAT_CMDS:
            log.info("Local command: repeat")
            if self._listen_timer and self._listen_timer.isActive():
                self._listen_timer.stop()
            if self._last_response:
                self._streaming_active = False
                self.sm.context.ghost_response = self._last_response
                self.sm.context.session_active = False
                self.sm.transition(State.SPEAKING)
            else:
                self.audio.speak_local("No hay nada que repetir.", blocking=False)
                self.sm.transition(State.IDLE)
            return True

        if t in _VOL_UP_CMDS or t in _VOL_DOWN_CMDS:
            step = 0.2 if t in _VOL_UP_CMDS else -0.2
            vol = self.audio.adjust_voice_volume(step)
            log.info("Local command: voice volume -> %.0f%%", vol * 100)
            self.audio.play_sound("ready")
            # Stay in LISTENING so the user can follow up with the real command.
            self._listen_timeout = 0
            return True

        return False

    def _on_listening_volume(self, vol: float):
        self.visual.set_audio_volume(vol)
        if vol > 0.05:
            self._last_activity = 0

    def _on_audio_spectrum(self, bins: list):
        self.visual.set_audio_spectrum(bins)

    def _on_listen_tick(self):
        self._listen_timeout += 1
        self._last_activity = 0
        # 18s is enough room for the user to think and answer a question after
        # SPEAKING ends; 10s was tripping users who pause before responding.
        if self._listen_timeout >= 18:
            self._listen_timer.stop()
            self.sm.transition(State.IDLE)

    def _on_ghost_sentence(self, sentence: str):
        """A streamed sentence arrived from the agent. First one flips
        PROCESSING → SPEAKING so the voice starts seconds before the run ends."""
        state = self.sm.state
        if state == State.PROCESSING:
            self._streaming_active = True
            self.audio.stream_begin()
            self.audio.stream_feed(sentence)
            self.sm.transition(State.SPEAKING)
        elif state == State.SPEAKING and self._streaming_active:
            self.audio.stream_feed(sentence)
        # Any other state: the turn was cancelled — drop the sentence.

    def _on_ghost_response(self, text: str, is_question: bool):
        if not APP_CONFIG.privacy_mode:
            logging.getLogger("ghost").debug("Response: %s | question=%s", text[:200], is_question)
        self.sm.context.ghost_response = text
        self.sm.context.session_active = is_question
        self._last_response = text
        self._last_activity = 0
        if self._streaming_active:
            # Already speaking the streamed sentences; just close the stream so
            # speech_finished fires when the queue drains.
            self.audio.stream_close()
        else:
            self.sm.transition(State.SPEAKING)

    @pyqtSlot(str)
    def _on_foreign_response(self, text: str):
        """A monitored session (the webchat) produced a reply. Queue it and try
        to read it; never interrupt the user's own voice turn."""
        if not text or not text.strip():
            return
        self._foreign_queue.append(text.strip())
        if not APP_CONFIG.privacy_mode:
            logging.getLogger("ghost").info("Webchat reply queued for read-aloud (%d chars)", len(text))
        self._flush_foreign_queue()

    def _flush_foreign_queue(self):
        """Speak the next queued webchat reply if we're idle and not talking."""
        if not self._foreign_queue:
            return
        if self.sm.state not in (State.IDLE, State.STANDBY):
            return
        if self.audio.is_speaking():
            return
        text = self._foreign_queue.pop(0)
        self._streaming_active = False
        self.sm.context.ghost_response = text
        self._last_response = text
        # Webchat conversation lives in text; don't open a voice LISTENING turn.
        self.sm.context.session_active = False
        self._last_activity = 0
        self.sm.transition(State.SPEAKING)

    def _on_ghost_error(self, error: str):
        logging.getLogger("ghost").error("Ghost error: %s", error)
        # If the run died mid-stream, the speech stream was never closed and
        # _tts_busy would stay True forever, wedging SPEAKING. Cut it here.
        if self._streaming_active:
            self.audio.stop_speaking()
            self._streaming_active = False
        # If the agent is unreachable, switch to STANDBY instead of speaking the raw error
        err_lower = error.lower()
        unreachable_tokens = (
            "no encontrado", "config", "timeout",
            "no respondió", "connection lost", "closed by server",
        )
        if any(tok in err_lower for tok in unreachable_tokens):
            self.sm.transition(State.STANDBY)
            self._standby_timer.start(30000)
            return
        self.sm.context.ghost_response = f"Error al contactar a {APP_CONFIG.agent_name}: {error}"
        self.sm.transition(State.SPEAKING)

    def _update_speech_volume(self):
        if self.audio.is_speaking():
            v = 0.3 + 0.4 * abs(math.sin(time_mod.time() * 8))
            self.visual.set_speech_volume(v)
        else:
            self.visual.set_speech_volume(0.0)

    def _on_speech_finished(self):
        self._check_speech_end()

    def _check_speech_end(self):
        # In a streamed turn the queue can be momentarily silent while the
        # next sentence synthesizes, but _tts_busy stays True until the stream
        # closes AND drains — so this check stays correct.
        if not self.audio.is_speaking():
            if self.sm.state == State.SPEAKING and self._streaming_active \
                    and self.ghost.is_busy():
                # Stream still open (run in flight, between sentences): wait.
                return
            self._streaming_active = False
            if self._speech_end_timer and self._speech_end_timer.isActive():
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
        self.audio.speak_local(f"{APP_CONFIG.agent_name} conectado.", blocking=False)
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
        """Place the overlay: saved position if still on-screen, else centered."""
        w = int(APP_CONFIG.overlay_width or 320)
        h = int(APP_CONFIG.overlay_height or 320)
        if (
            not APP_CONFIG.overlay_centered
            and APP_CONFIG.overlay_x is not None
            and APP_CONFIG.overlay_y is not None
        ):
            rect = QRect(int(APP_CONFIG.overlay_x), int(APP_CONFIG.overlay_y), w, h)
            # La posición guardada puede quedar fuera tras cambiar de monitor
            if any(s.geometry().intersects(rect) for s in QApplication.screens()):
                self.setGeometry(rect)
                return
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
        else:
            geo = QRect(0, 0, 1920, 1080)
        x = geo.x() + (geo.width() - w) // 2
        y = geo.y() + (geo.height() - h) // 2
        self.setGeometry(x, y, w, h)

    # ---- Click-through ------------------------------------------------
    def _effective_click_through(self) -> bool:
        return bool(APP_CONFIG.click_through) and not self._move_mode

    def _apply_click_through(self, enabled: bool):
        """Let mouse input pass through the overlay to the windows beneath.

        On Windows the WS_EX_TRANSPARENT exstyle bit is flipped on the live
        HWND (like Electron's setIgnoreMouseEvents): no window re-creation,
        no flicker, and the GL context survives. Elsewhere we fall back to
        Qt's WindowTransparentForInput flag, which re-creates the window.
        """
        if sys.platform == "win32":
            try:
                import win32gui
                import win32con
                hwnd = int(self.winId())
                ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                if enabled:
                    new = ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT
                else:
                    # WS_EX_LAYERED se queda: quitarlo cambia el modo de
                    # render; solo la transparencia de hit-test se va.
                    new = ex & ~win32con.WS_EX_TRANSPARENT
                if new != ex:
                    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, new)
                    if enabled and not (ex & win32con.WS_EX_LAYERED):
                        # Una ventana que recién gana WS_EX_LAYERED no se
                        # renderiza hasta esta llamada.
                        win32gui.SetLayeredWindowAttributes(
                            hwnd, 0, 255, win32con.LWA_ALPHA
                        )
            except Exception as e:
                logging.getLogger("overlay").warning("click-through: %s", e)
        else:
            self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
            if self.isVisible():
                self.show()

    def _set_move_mode(self, enabled: bool):
        self._move_mode = bool(enabled)
        self._apply_click_through(self._effective_click_through())
        # Refresh the persistent tray copy so its checkmark stays in sync
        self._tray_menu = self._build_tray_menu()
        self.tray.setContextMenu(self._tray_menu)

    def _center_overlay(self):
        APP_CONFIG.overlay_centered = True
        APP_CONFIG.overlay_x = None
        APP_CONFIG.overlay_y = None
        APP_CONFIG.save()
        self._position_overlay()

    def _set_overlay_size(self, px: int):
        APP_CONFIG.overlay_width = px
        APP_CONFIG.overlay_height = px
        APP_CONFIG.save()
        self._position_overlay()
        self._tray_menu = self._build_tray_menu()
        self.tray.setContextMenu(self._tray_menu)

    def showEvent(self, event):
        super().showEvent(event)
        # En cada show: cubre el primer show, el "Mostrar" del tray, y
        # re-aplica el exstyle si el HWND nativo se recreó.
        self._apply_click_through(self._effective_click_through())

    def mousePressEvent(self, event):
        self._drag_pos = event.globalPosition().toPoint()
        self._dragged = False

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(
                self.pos() + event.globalPosition().toPoint() - self._drag_pos
            )
            self._drag_pos = event.globalPosition().toPoint()
            self._dragged = True

    def mouseReleaseEvent(self, event):
        # Persistir la posición solo al soltar (save() re-encripta secretos
        # vía DPAPI; no debe llamarse por cada mouse-move).
        if event.button() == Qt.MouseButton.LeftButton and self._dragged:
            self._dragged = False
            APP_CONFIG.overlay_x = self.x()
            APP_CONFIG.overlay_y = self.y()
            APP_CONFIG.overlay_centered = False
            APP_CONFIG.save()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self.tray.showMessage(
            f"{APP_CONFIG.agent_name} Jarvis",
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
