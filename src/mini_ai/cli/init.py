"""初始化脚本：首次安装时自动生成配置"""

import os
import shutil
from pathlib import Path
from typing import Optional

from ..logger import logger


def get_data_dir() -> Path:
    """获取数据目录"""
    return Path(os.environ.get("MINI_AI_DATA", Path.home() / ".mini_ai"))


def get_global_memory_dir() -> Path:
    """获取全局记忆目录"""
    return get_data_dir() / "memory"


def init_mini_ai(force: bool = False, username: Optional[str] = None) -> bool:
    """初始化 mini-ai
    
    Args:
        force: 是否强制覆盖现有配置
        username: 用户名（可选）
    
    Returns:
        True 如果进行了初始化，False 如果跳过
    """
    data_dir = get_data_dir()
    global_memory_dir = get_global_memory_dir()
    
    # 检查是否已初始化
    config_file = data_dir / "config.yaml"
    if config_file.exists() and not force:
        logger.info(f"[Init] mini-ai 已初始化，跳过（使用 --force 强制覆盖）")
        return False
    
    logger.info("[Init] 开始初始化 mini-ai...")
    
    # 1. 创建目录结构
    data_dir.mkdir(parents=True, exist_ok=True)
    global_memory_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. 生成 config.yaml
    _init_config(data_dir, force)
    
    # 3. 生成全局 SOUL.md 和 RULES.md
    _init_character(global_memory_dir, force)
    
    # 4. 初始化用户目录（如果提供了 username）
    if username:
        _init_user(username)
    
    # 5. 生成 .gitignore（避免敏感信息泄露）
    _init_gitignore(data_dir)
    
    logger.info(f"[Init] ✅ 初始化完成！配置文件位置: {data_dir}")
    logger.info("[Init] 请编辑 config.yaml 填写 API Key")
    
    return True


def _init_config(data_dir: Path, force: bool) -> None:
    """初始化配置文件"""
    from ..config import PACKAGE_DIR
    
    config_file = data_dir / "config.yaml"
    template_file = PACKAGE_DIR / "templates" / "config.yaml"
    
    if not template_file.exists():
        logger.warning(f"[Init] 配置模板不存在: {template_file}")
        return
    
    if config_file.exists() and not force:
        logger.debug(f"[Init] 配置文件已存在，跳过: {config_file}")
        return
    
    shutil.copy2(template_file, config_file)
    logger.info(f"[Init] 生成配置文件: {config_file}")


def _init_character(memory_dir: Path, force: bool) -> None:
    """初始化身份定义和行为规范"""
    from ..config import PACKAGE_DIR
    
    templates_dir = PACKAGE_DIR / "templates"
    
    for name in ["SOUL.md", "RULES.md"]:
        src = templates_dir / name
        dst = memory_dir / name
        
        if not src.exists():
            logger.warning(f"[Init] 模板不存在: {src}")
            continue
        
        if dst.exists() and not force:
            logger.debug(f"[Init] 文件已存在，跳过: {dst}")
            continue
        
        shutil.copy2(src, dst)
        logger.info(f"[Init] 生成默认配置: {dst}")


def _init_user(username: str) -> None:
    """初始化用户目录"""
    from ..config import user_data_dir
    
    user_dir = user_data_dir(username)
    user_memory_dir = user_dir / "memory"
    user_memory_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"[Init] 创建用户目录: {user_dir}")


def _init_gitignore(data_dir: Path) -> None:
    """生成 .gitignore"""
    gitignore_file = data_dir / ".gitignore"
    
    if gitignore_file.exists():
        return
    
    content = """# mini-ai 数据目录
# 请勿提交到版本控制

# 敏感信息
config.yaml
*.key
*.pem

# 数据库
*.db
*.sqlite

# 日志
*.log

# 临时文件
*.tmp
*.bak
__pycache__/
"""
    
    gitignore_file.write_text(content, encoding="utf-8")
    logger.debug(f"[Init] 生成 .gitignore: {gitignore_file}")


def check_and_init() -> bool:
    """检查并自动初始化（启动时调用）
    
    Returns:
        True 如果已初始化或刚完成初始化，False 如果初始化失败
    """
    try:
        init_mini_ai(force=False)
        return True
    except Exception as e:
        logger.error(f"[Init] 初始化失败: {e}", exc_info=True)
        return False


# CLI 命令入口
def main():
    """CLI 入口：mini-ai init"""
    import argparse
    
    parser = argparse.ArgumentParser(description="初始化 mini-ai")
    parser.add_argument("--force", action="store_true", help="强制覆盖现有配置")
    parser.add_argument("--username", type=str, help="用户名")
    
    args = parser.parse_args()
    
    init_mini_ai(force=args.force, username=args.username)


if __name__ == "__main__":
    main()
