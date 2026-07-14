"""Floating real-time voice capture log window for Ghost Jarvis."""
import logging

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

_MAX_BLOCKS = 300

# Level → color
_LEVEL_COLOR = {
    logging.DEBUG:    "#666666",
    logging.INFO:     "#b0b8c8",
    logging.WARNING:  "#f0c040",
    logging.ERROR:    "#ff5555",
    logging.CRITICAL: "#ff2020",
}

# Substring match overrides (first match wins, case-insensitive check on lowered msg)
_KW_COLORS = [
    ("wake check:",    "#69f0ae"),   # fuzzy score line
    ("hotword",        "#b9f6ca"),   # hotword+prompt direct
    ("stt (long):",    "#80d8ff"),   # long utterance transcript
    ("stt:",           "#00e5ff"),   # normal transcript
    ("stt filtered",   "#555577"),   # filtered out
    ("-> listening",   "#a5d6a7"),
    ("-> processing",  "#fff59d"),
    ("-> speaking",    "#ffcc80"),
    ("-> wake",        "#f48fb1"),
    ("-> idle",        "#ce93d8"),
    ("-> standby",     "#ef9a9a"),
    ("idle ignore",    "#555566"),
    ("standby ignore", "#555566"),
    ("gateway",        "#ff8a65"),
    ("error",          "#ff5555"),
    ("warning",        "#f0c040"),
]


class _QtLogEmitter(QObject):
    """Internal QObject that owns the pyqtSignal so the handler itself is a
    plain Python object and never raises RuntimeError during logging.shutdown()."""
    new_record = pyqtSignal(int, str)  # levelno, formatted text


class QtLogHandler(logging.Handler):
    """Logging handler that forwards records as Qt signals (thread-safe).

    The handler self-detaches from the root logger on close() and tolerates
    being called after its underlying QObject has been deleted by Qt — that
    happens when VoiceLogWindow is destroyed before the logger is shut down.
    """

    def __init__(self):
        super().__init__()
        self._emitter = _QtLogEmitter()
        self.setFormatter(
            logging.Formatter("%(asctime)s [%(name)-8s] %(message)s", datefmt="%H:%M:%S")
        )
        self._alive = True

    @property
    def new_record(self):
        return self._emitter.new_record

    def emit(self, record: logging.LogRecord):
        if not self._alive:
            return
        try:
            self._emitter.new_record.emit(record.levelno, self.format(record))
        except RuntimeError:
            # Underlying C++ QObject already deleted; detach silently.
            self._alive = False
            try:
                logging.getLogger().removeHandler(self)
            except Exception:
                pass
        except Exception:
            pass

    def close(self):
        self._alive = False
        try:
            logging.getLogger().removeHandler(self)
        except Exception:
            pass
        try:
            super().close()
        except Exception:
            pass


class VoiceLogWindow(QWidget):
    def __init__(self, handler: QtLogHandler, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setWindowTitle("Ghost Jarvis — Log de captura de voz")
        self.resize(660, 420)
        self._entry_count = 0

        self.setStyleSheet("""
            QWidget          { background:#0d1117; color:#c9d1d9; }
            QPlainTextEdit   {
                background:#0d1117; color:#c9d1d9;
                font-family:Consolas,Courier New,monospace; font-size:11px;
                border:1px solid #30363d; border-radius:4px;
            }
            QPushButton {
                background:#21262d; color:#c9d1d9;
                border:1px solid #30363d; padding:4px 12px; border-radius:4px;
            }
            QPushButton:hover { background:#30363d; }
            QLabel { color:#8b949e; font-size:10px; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # ── header bar ──────────────────────────────────────────────────────
        hdr = QHBoxLayout()
        title = QLabel("Captura de voz en tiempo real  ·  colores: <span style='color:#00e5ff'>STT</span>  "
                        "<span style='color:#69f0ae'>wake score</span>  "
                        "<span style='color:#f0c040'>warning</span>  "
                        "<span style='color:#ff5555'>error</span>")
        title.setTextFormat(Qt.TextFormat.RichText)
        hdr.addWidget(title)
        hdr.addStretch()
        btn_clear = QPushButton("Limpiar")
        btn_clear.setFixedWidth(72)
        btn_clear.clicked.connect(self._clear)
        hdr.addWidget(btn_clear)
        root.addLayout(hdr)

        # ── log display ─────────────────────────────────────────────────────
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(_MAX_BLOCKS)
        root.addWidget(self._text)

        # ── status bar ──────────────────────────────────────────────────────
        self._status = QLabel("0 entradas")
        root.addWidget(self._status)

        handler.new_record.connect(self._append)

    # ── slots ────────────────────────────────────────────────────────────────

    def _append(self, level: int, message: str):
        color = _LEVEL_COLOR.get(level, "#b0b8c8")
        msg_lower = message.lower()
        for kw, kw_color in _KW_COLORS:
            if kw in msg_lower:
                color = kw_color
                break

        cur = self._text.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cur.setCharFormat(fmt)
        cur.insertText(message + "\n")
        self._text.setTextCursor(cur)
        self._text.ensureCursorVisible()

        self._entry_count += 1
        self._status.setText(f"{self._entry_count} entradas")

    def _clear(self):
        self._text.clear()
        self._entry_count = 0
        self._status.setText("0 entradas")
