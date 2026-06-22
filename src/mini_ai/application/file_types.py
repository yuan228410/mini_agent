"""File service type/language detection helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..utils import _UTC8

EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".vue": "vue", ".html": "html", ".css": "css", ".scss": "scss",
    ".less": "less", ".sass": "sass",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".rs": "rust", ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp", ".rb": "ruby", ".php": "php", ".sql": "sql",
    ".xml": "xml", ".svg": "xml", ".dockerfile": "dockerfile",
    ".gitignore": "plaintext", ".env": "plaintext", ".txt": "plaintext",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala",
    ".r": "r", ".lua": "lua", ".dart": "dart",
    ".erl": "erlang", ".ex": "elixir", ".exs": "elixir",
    ".clj": "clojure", ".groovy": "groovy",
    ".tf": "hcl", ".proto": "protobuf",
    ".conf": "ini", ".ini": "ini", ".cfg": "ini",
    ".cmake": "cmake", ".patch": "diff", ".diff": "diff",
    ".prisma": "prisma", ".elm": "elm",
    ".tex": "latex", ".sty": "tex",
    ".graphql": "graphql", ".gql": "graphql",
    ".vim": "vim",
    ".lock": "plaintext", ".dockerignore": "plaintext",
    ".editorconfig": "ini", ".prettierrc": "json",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".bz2", ".xz",
    ".exe", ".dll", ".so", ".dylib", ".bin",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav", ".flac", ".ogg",
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
    ".pyc", ".pyo", ".class", ".o", ".obj", ".a",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ipynb",
}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg"}

IMAGE_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".webp": "image/webp", ".svg": "image/svg+xml",
}

IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".tox", ".egg-info"}


def is_binary_file(filepath: Path, ext: str) -> bool:
    """Detect whether a file is binary from extension and a content sample."""

    if ext in BINARY_EXTENSIONS:
        return True
    if ext in IMAGE_EXTENSIONS:
        return True
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(1024)
        if b"\x00" in chunk:
            return True
        non_printable = sum(1 for b in chunk if b < 0x20 and b not in (0x09, 0x0A, 0x0D))
        return non_printable > len(chunk) * 0.3
    except Exception:
        return False


def format_time(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=_UTC8).isoformat()
