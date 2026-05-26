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

_fh = logging.FileHandler(log_file, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(_fmt)

_ch = logging.StreamHandler()
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter("%(message)s"))

logger.addHandler(_fh)
logger.addHandler(_ch)
