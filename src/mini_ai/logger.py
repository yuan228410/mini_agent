"""日志模块"""
import logging
import os
import threading
import contextvars
from datetime import datetime
from pathlib import Path

try:
    from .config import DATA_DIR
    _base = DATA_DIR
except Exception:
    _base = Path.home() / ".mini_ai"
LOG_DIR = _base / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOG_DIR / f"{datetime.now().strftime('%Y%m%d')}.log"

logger = logging.getLogger("agent")
logger.setLevel(logging.DEBUG)

# 使用 contextvars 存储当前会话 ID（线程安全）
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("session_id", default=None)


def set_session_id(session_id: str | None):
    """设置当前会话 ID（用于日志跟踪）"""
    _current_session_id.set(session_id)


def get_session_id() -> str | None:
    """获取当前会话 ID"""
    return _current_session_id.get()


class _ThreadFormatter(logging.Formatter):
    def format(self, record):
        # PID 缩短为后 5 位
        record.pid = os.getpid() % 100000
        
        # 线程名简化
        tid = threading.current_thread().name
        if tid.startswith("Thread-"):
            tid = f"T{threading.get_ident() % 10000:04d}"
        elif tid.startswith("ThreadPoolExecutor-"):
            # ThreadPoolExecutor-0_0 → TP-0_0
            tid = "TP-" + tid.split("-")[1]
        elif tid == "MainThread":
            tid = "Main"
        record.tid = tid
        
        # 添加 session_id 支持（从 contextvars 获取）
        session_id = get_session_id()
        if session_id:
            record.session = session_id[:8]  # 只显示前 8 位
        else:
            record.session = "-"
        
        return super().format(record)


_fmt = _ThreadFormatter("%(asctime)s [%(levelname)s] [%(pid)d/%(tid)s] [%(session)s] %(message)s", datefmt="%H:%M:%S")

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
