"""测试初始化脚本"""
import os
import tempfile
from pathlib import Path

from mini_ai.cli.init import init_mini_ai, get_data_dir


def test_init_mini_ai():
    """测试初始化流程"""
    # 使用临时目录
    with tempfile.TemporaryDirectory() as tmpdir:
        # 设置环境变量
        os.environ["MINI_AI_DATA"] = tmpdir
        
        # 执行初始化
        result = init_mini_ai(force=True)
        assert result is True
        
        # 检查文件是否创建
        data_dir = Path(tmpdir)
        config_file = data_dir / "config.yaml"
        assert config_file.exists()
        
        memory_dir = data_dir / "memory"
        soul_file = memory_dir / "SOUL.md"
        rules_file = memory_dir / "RULES.md"
        
        assert soul_file.exists()
        assert rules_file.exists()
        
        # 检查内容
        soul_content = soul_file.read_text(encoding="utf-8")
        assert "mini_ai" in soul_content
        assert "AI 编程助手" in soul_content
        
        # RULES.md 现在是可选的自定义文件，不再检查内容
        
        # 测试重复初始化（不强制）
        result = init_mini_ai(force=False)
        assert result is False  # 应该跳过
        
        # 清理环境变量
        os.environ.pop("MINI_AI_DATA", None)


def test_init_with_username():
    """测试带用户名的初始化"""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["MINI_AI_DATA"] = tmpdir
        
        # 执行初始化（带用户名）
        result = init_mini_ai(force=True, username="test_user")
        assert result is True
        
        # 检查用户目录
        from mini_ai.config import user_data_dir
        user_dir = user_data_dir("test_user")
        # user_data_dir 会自动创建目录
        assert user_dir.exists()
        
        user_memory_dir = user_dir / "memory"
        assert user_memory_dir.exists()
        
        os.environ.pop("MINI_AI_DATA", None)


if __name__ == "__main__":
    test_init_mini_ai()
    test_init_with_username()
    print("✅ 所有测试通过")
