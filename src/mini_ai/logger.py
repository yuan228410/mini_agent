"""日志模块"""
import logging
import os
import threading
from datetime import datetime
from pathlib import Path

LOG_DIR = Path.home() / ".mini_ai" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}.log"

logger = logging.getLogger("agent")
logger.setLevel(logging.DEBUG)


class _ThreadFormatter(logging.Formatter):
    def format(self, record):
        record.pid = os.getpid()
        record.tid = threading.current_thread().name
        if record.tid.startswith("Thread-"):
            record.tid = f"T{threading.get_ident() % 10000:04d}"
        return super().format(record)


_fmt = _ThreadFormatter("%(asctime)s [%(levelname)s] [%(pid)d/%(tid)s] %(message)s", datefmt="%H:%M:%S")

_LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}


def _get_console_level():
    if os.environ.get("MINI_AI_DEBUG") == "1":
        return logging.DEBUG
    try:
        from .config import LOGGING
        level_str = LOGGING.get("level", "WARNING").upper()
        return _LEVEL_MAP.get(level_str, logging.WARNING)
    except Exception:
        return logging.WARNING


def _get_file_level():
    try:
        from .config import LOGGING
        level_str = LOGGING.get("file_level", "DEBUG").upper()
        return _LEVEL_MAP.get(level_str, logging.DEBUG)
    except Exception:
        return logging.DEBUG


class _LazyFileHandler(logging.FileHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setFormatter(_fmt)

    def emit(self, record):
        if record.levelno >= _get_file_level():
            super().emit(record)


class _LazyConsoleHandler(logging.StreamHandler):
    def __init__(self):
        super().__init__()
        self.setLevel(logging.WARNING)
        self.setFormatter(_fmt)

    def emit(self, record):
        if record.levelno >= _get_console_level():
            super().emit(record)


_fh = _LazyFileHandler(log_file, encoding="utf-8")
_ch = _LazyConsoleHandler()

logger.addHandler(_fh)
logger.addHandler(_ch)
