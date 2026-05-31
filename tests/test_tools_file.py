"""测试文件工具"""
import pytest
from pathlib import Path

from mini_ai.tools.read_file import execute as read_file
from mini_ai.tools.write_file import execute as write_file
from mini_ai.tools.delete_file import execute as delete_file


class TestReadFile:
    """测试 read_file 工具"""
    
    def test_read_existing_file(self, temp_workspace):
        """读取存在的文件"""
        result = read_file({"path": str(temp_workspace / "README.md")})
        assert "Test Project" in result
        assert "|" in result  # 行号格式
    
    def test_read_missing_file(self, temp_workspace):
        """读取不存在的文件"""
        result = read_file({"path": str(temp_workspace / "missing.txt")})
        assert "Error" in result
        assert "不存在" in result
    
    def test_read_directory(self, temp_workspace):
        """读取目录（应报错）"""
        result = read_file({"path": str(temp_workspace / "src")})
        assert "Error" in result
        assert "不是文件" in result
    
    def test_read_with_line_range(self, temp_workspace):
        """按行号范围读取"""
        result = read_file({
            "path": str(temp_workspace / "README.md"),
            "start_line": 1,
            "end_line": 1
        })
        lines = result.split("\n")
        assert len(lines) == 1
    
    def test_read_missing_path_param(self):
        """缺少 path 参数"""
        result = read_file({})
        assert "Error" in result
        assert "缺少 path 参数" in result


class TestWriteFile:
    """测试 write_file 工具"""
    
    def test_write_new_file(self, temp_workspace):
        """写入新文件"""
        file_path = temp_workspace / "new_file.txt"
        result = write_file({
            "path": str(file_path),
            "content": "Hello, World!"
        })
        assert "已写入" in result
        assert file_path.exists()
        assert file_path.read_text() == "Hello, World!"
    
    def test_write_overwrite(self, temp_workspace):
        """覆盖写入"""
        file_path = temp_workspace / "test.txt"
        write_file({"path": str(file_path), "content": "First"})
        result = write_file({
            "path": str(file_path),
            "content": "Second",
            "mode": "overwrite"
        })
        assert "已写入" in result
        assert file_path.read_text() == "Second"
    
    def test_write_append(self, temp_workspace):
        """追加写入"""
        file_path = temp_workspace / "test.txt"
        write_file({"path": str(file_path), "content": "First\n"})
        result = write_file({
            "path": str(file_path),
            "content": "Second",
            "mode": "append"
        })
        assert "已追加" in result
        assert file_path.read_text() == "First\nSecond"
    
    def test_write_creates_parent_dirs(self, temp_workspace):
        """自动创建父目录"""
        file_path = temp_workspace / "deeply" / "nested" / "file.txt"
        result = write_file({
            "path": str(file_path),
            "content": "Content"
        })
        assert "已写入" in result
        assert file_path.exists()
    
    def test_write_too_large(self, temp_workspace):
        """写入过大内容"""
        file_path = temp_workspace / "large.txt"
        large_content = "x" * (11 * 1024 * 1024)  # 11MB
        result = write_file({
            "path": str(file_path),
            "content": large_content
        })
        assert "Error" in result
        assert "过大" in result


class TestDeleteFile:
    """测试 delete_file 工具"""
    
    def test_delete_file(self, temp_workspace):
        """删除文件"""
        file_path = temp_workspace / "to_delete.txt"
        file_path.write_text("delete me")
        
        # delete_file 有安全检查，禁止删除 /var 等系统路径
        # 临时目录在 macOS 上是 /var/folders，需要 mock 安全检查
        from mini_ai.tools import delete_file as df_module
        original_is_blocked = df_module._is_blocked
        
        def mock_is_blocked(path):
            # 只禁止真正的系统路径，允许临时目录
            if "/tmp" in str(path) or "/var/folders" in str(path):
                return False
            return original_is_blocked(path)
        
        df_module._is_blocked = mock_is_blocked
        try:
            result = delete_file({"path": str(file_path)})
            assert "已删除" in result
            assert not file_path.exists()
        finally:
            df_module._is_blocked = original_is_blocked
    
    def test_delete_missing_file(self, temp_workspace):
        """删除不存在的文件"""
        result = delete_file({"path": str(temp_workspace / "missing.txt")})
        assert "Error" in result
    
    def test_delete_directory_recursive(self, temp_workspace):
        """递归删除目录"""
        dir_path = temp_workspace / "test_dir"
        dir_path.mkdir()
        (dir_path / "file.txt").write_text("content")
        
        # mock 安全检查
        from mini_ai.tools import delete_file as df_module
        original_is_blocked = df_module._is_blocked
        
        def mock_is_blocked(path):
            if "/tmp" in str(path) or "/var/folders" in str(path):
                return False
            return original_is_blocked(path)
        
        df_module._is_blocked = mock_is_blocked
        try:
            result = delete_file({
                "path": str(dir_path),
                "recursive": True
            })
            assert "已递归删除" in result
            assert not dir_path.exists()
        finally:
            df_module._is_blocked = original_is_blocked
