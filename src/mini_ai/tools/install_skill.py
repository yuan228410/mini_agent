"""技能安装工具：压缩包下载/本地路径 或 内联内容，支持三层级安装"""
import contextvars
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..config import TIMEOUTS
from ..logger import logger

_loader_var = contextvars.ContextVar("skill_loader", default=None)
_loader = None


def configure(loader=None):
    global _loader
    if loader is not None:
        _loader = loader
        _loader_var.set(loader)


def _get_loader():
    return _loader_var.get() or _loader


definition = {
    "type": "function",
    "function": {
        "name": "install_skill",
        "description": "安装技能。source（压缩包地址）或 content（Markdown内容）二选一。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称"},
                "source": {"type": "string", "description": "压缩包地址（URL 或本地路径）"},
                "content": {"type": "string", "description": "技能内容（Markdown）"},
                "level": {"type": "string", "enum": ["global", "user", "workspace"], "description": "安装层级，默认 user"},
            },
            "required": ["name"],
        },
    },
}


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _download(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=TIMEOUTS["web_fetch"]) as resp:
        suffix = _guess_suffix(url, resp)
        fd, path = tempfile.mkstemp(suffix=suffix, prefix="mini_ai_skill_")
        with open(fd, "wb") as f:
            f.write(resp.read())
    return path


def _guess_suffix(url: str, resp) -> str:
    for s in (".tar.gz", ".tgz", ".tar.bz2", ".zip"):
        if url.lower().endswith(s):
            return s
    ct = resp.headers.get("Content-Type", "")
    if "zip" in ct:
        return ".zip"
    if "tar" in ct:
        return ".tar.gz"
    if url.lower().endswith(".tar"):
        return ".tar"
    return ".zip"


def _extract(archive_path: str, dest_dir: Path) -> None:
    lower = archive_path.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)
    elif lower.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive_path, "r:gz") as tf:
            tf.extractall(dest_dir)
    elif lower.endswith(".tar.bz2"):
        with tarfile.open(archive_path, "r:bz2") as tf:
            tf.extractall(dest_dir)
    elif lower.endswith(".tar"):
        with tarfile.open(archive_path, "r:") as tf:
            tf.extractall(dest_dir)
    else:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest_dir)


def _flatten_if_needed(dest_dir: Path) -> None:
    if (dest_dir / "SKILL.md").exists():
        return
    for child in dest_dir.iterdir():
        if child.is_dir() and (child / "SKILL.md").exists():
            for item in child.iterdir():
                shutil.move(str(item), str(dest_dir / item.name))
            child.rmdir()
            return


def _validate(dest_dir: Path) -> bool:
    if (dest_dir / "SKILL.md").exists():
        return True
    for f in dest_dir.glob("*/SKILL.md"):
        return True
    return False


def _skill_summary(name: str) -> str:
    loader = _get_loader()
    if not loader or name not in loader.skills:
        return ""
    meta = loader.skills[name]["meta"]
    tier = loader.skills[name].get("tier", "")
    info = ""
    desc = meta.get("description", "")
    tags = meta.get("tags", "")
    if desc:
        info = f"\n描述: {desc}"
    if tags:
        info += f"\n标签: {tags}"
    if tier:
        info += f"\n层级: {tier}"
    return info


def _install_from_content(name: str, content: str, dest_dir: Path) -> str:
    skill_file = dest_dir / "SKILL.md"
    dest_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")
    loader = _get_loader()
    if loader:
        loader._load_all()
    logger.info(f"[安装技能] {name} → {skill_file} ({len(content)} 字符)")
    summary = _skill_summary(name)
    return f"技能 '{name}' 已安装到 {skill_file}{summary}\n\n请使用 load_skill 读取该技能的完整内容，了解其功能和使用方式。"


def _install_from_archive(name: str, source: str, dest_dir: Path) -> str:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)

    archive_path = None
    is_temp = False
    try:
        if _is_url(source):
            logger.info(f"[安装技能] 下载 {source}")
            archive_path = _download(source)
            is_temp = True
        else:
            local = Path(source).expanduser().resolve()
            if not local.exists():
                return f"Error: 本地文件不存在: {source}"
            if not (local.is_file() and (local.suffix in (".zip", ".gz", ".bz2") or local.name.endswith(".tar.gz") or local.name.endswith(".tar.bz2"))):
                return f"Error: 不支持的文件格式: {source}"
            archive_path = str(local)
            is_temp = False

        logger.info(f"[安装技能] 解压到 {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        _extract(archive_path, dest_dir)
        _flatten_if_needed(dest_dir)

        if not _validate(dest_dir):
            shutil.rmtree(dest_dir)
            return f"Error: 压缩包中未找到 SKILL.md，技能 '{name}' 安装失败"

        loader = _get_loader()
        if loader:
            loader._load_all()

        logger.info(f"[安装技能] {name} 安装成功")
        summary = _skill_summary(name)
        return f"技能 '{name}' 已安装到 {dest_dir}{summary}\n\n请使用 load_skill 读取该技能的完整内容，了解其功能和使用方式。"
    except Exception as e:
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        logger.error(f"[安装技能] {name} 安装失败: {e}")
        return f"Error: 技能 '{name}' 安装失败 - {e}"
    finally:
        if is_temp and archive_path:
            Path(archive_path).unlink(missing_ok=True)


def execute(args: dict) -> str:
    name = args.get("name", "")
    source = args.get("source")
    content = args.get("content")
    level = args.get("level", "user")

    loader = _get_loader()
    if not loader:
        return "Error: 技能加载器未配置"

    logger.debug(f"[安装技能] loader tiers: {[(t, str(p)) for t, p in loader._tier_paths]}, level={level}")

    target_dir = loader.get_tier_dir(level)
    if not target_dir:
        fallback = loader.skills_dir
        target_dir = fallback
        logger.warning(f"[安装技能] 层级 '{level}' 未配置，回退到 global 目录 {fallback}。安装后技能层级显示为 global")

    if not source and not content:
        return "Error: 必须提供 source（压缩包地址）或 content（技能内容）参数"

    dest_dir = Path(target_dir) / name

    if content:
        return _install_from_content(name, content, dest_dir)
    return _install_from_archive(name, source, dest_dir)
