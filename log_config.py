"""Logging configuration for Ghost Jarvis.

Provides structured, timestamped logs to logs/ghost-jarvis.log.
Console output is preserved for development; file logging is always active.

Auto-cleans log files older than a configurable number of days on startup.
"""
import logging
import sys
import time
from pathlib import Path
from logging.handlers import RotatingFileHandler


class _StreamToLogger:
    """Redirect a text stream into the Python logging system so stdout/stderr
    also go through the rotating file handler (avoids unbounded app.log).

    Guarded against recursion: if logging itself fails, the error handler
    writes to the original stderr instead of looping back into this object.
    """
    def __init__(self, logger_name: str, level: int = logging.INFO):
        self._logger = logging.getLogger(logger_name)
        self._level = level
        self._in_write = False

    def write(self, buf: str) -> None:
        if self._in_write or not buf:
            return
        self._in_write = True
        try:
            for line in buf.rstrip().splitlines():
                self._logger.log(self._level, line.rstrip())
        except Exception:
            # Prevent recursion: if logging fails, write to original stderr
            try:
                sys.__stderr__.write(buf)
            except Exception:
                pass
        finally:
            self._in_write = False

    def flush(self) -> None:
        pass

LOG_DIR = Path(__file__).with_name("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "ghost-jarvis.log"
APP_DIR = Path(__file__).parent

# Keep last ~5 MB per file, up to 3 backups
_file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(
    logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)


def _clean_old_logs(
    directory: Path,
    days: int = 3,
    patterns: tuple[str, ...] = ("*.log", "*.log.*"),
) -> int:
    """Remove log files in *directory* matching *patterns* older than *days*.

    Returns the number of files removed.
    """
    if not directory.exists():
        return 0

    cutoff = time.time() - (days * 86400)
    removed = 0

    for pattern in patterns:
        for fpath in directory.glob(pattern):
            if not fpath.is_file():
                continue
            try:
                if fpath.stat().st_mtime < cutoff:
                    fpath.unlink()
                    removed += 1
            except OSError:
                pass
    return removed


def redirect_stdout_stderr() -> None:
    """Send stdout/stderr into the Python logger so they are subject to the
    same RotatingFileHandler size limits (prevents unbounded app.log growth).
    """
    sys.stdout = _StreamToLogger("stdout", logging.INFO)
    sys.stderr = _StreamToLogger("stderr", logging.ERROR)


def setup_logging(level: int = logging.INFO, log_retention_days: int = 3) -> None:
    """Configure root logger and redirect stdout print storms."""
    # Force UTF-8 on Windows console so non-latin characters in logs don't crash
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    # Clean old log files on startup (logs/ directory + app.log in app root)
    cleaned_logs = _clean_old_logs(LOG_DIR, days=log_retention_days)
    try:
        app_log = APP_DIR / "app.log"
        if app_log.exists() and app_log.stat().st_mtime < (time.time() - log_retention_days * 86400):
            app_log.unlink()
            cleaned_logs += 1
    except OSError:
        pass

    if cleaned_logs:
        # Use a temporary logger to stderr so we don't need the root logger yet
        print(f"[Ghost Jarvis] Cleaned {cleaned_logs} old log file(s) (> {log_retention_days} days)", file=sys.stderr)

    root = logging.getLogger()
    root.setLevel(level)

    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        root.addHandler(_file_handler)

    # Optional console handler (useful when running from terminal)
    if not any(isinstance(h, logging.StreamHandler) and h.stream is sys.stdout for h in root.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )
        root.addHandler(console)

    redirect_stdout_stderr()
