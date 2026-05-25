"""技能安装工具：压缩包下载/本地路径 或 内联内容"""
import shutil
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..config import TIMEOUTS
from ..logger import logger

_loader = None
_skills_dir = None


def configure(loader=None):
    global _loader, _skills_dir
    if loader is not None:
        _loader = loader
        _skills_dir = loader.skills_dir


definition = {
    "type": "function",
    "function": {
        "name": "install_skill",
        "description": "安装技能到技能目录。支持两种方式：1) source 参数指定压缩包地址（URL 或本地路径，zip/tar.gz/tar.bz2）；2) content 参数直接传入技能内容（Markdown，可含 YAML frontmatter）。安装后可通过 load_skill 加载使用",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称，英文字母和连字符"},
                "source": {"type": "string", "description": "技能压缩包地址（URL 或本地路径），与 content 二选一"},
                "content": {"type": "string", "description": "技能内容（Markdown 格式，可含 YAML frontmatter），与 source 二选一"},
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
    for f in dest_dir.rglob("SKILL.md"):
        return True
    return False


def _skill_summary(name: str) -> str:
    if not _loader or name not in _loader.skills:
        return ""
    meta = _loader.skills[name]["meta"]
    info = ""
    desc = meta.get("description", "")
    tags = meta.get("tags", "")
    if desc:
        info = f"\n描述: {desc}"
    if tags:
        info += f"\n标签: {tags}"
    return info


def _install_from_content(name: str, content: str, dest_dir: Path) -> str:
    skill_file = dest_dir / "SKILL.md"
    dest_dir.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(content, encoding="utf-8")
    if _loader:
        _loader._load_all()
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

        if _loader:
            _loader._load_all()

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
    name = args["name"]
    source = args.get("source")
    content = args.get("content")

    if not _skills_dir:
        return "Error: 技能目录未配置"

    if not source and not content:
        return "Error: 必须提供 source（压缩包地址）或 content（技能内容）参数"

    dest_dir = Path(_skills_dir) / name

    if content:
        return _install_from_content(name, content, dest_dir)
    return _install_from_archive(name, source, dest_dir)
