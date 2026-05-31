"""测试工作空间管理"""
import tempfile
import shutil
from pathlib import Path
import pytest

from mini_ai.workspace import Workspace, WorkspaceManager


class TestWorkspace:
    """测试 Workspace 类"""
    
    def test_workspace_properties(self):
        """工作空间属性正确"""
        ws_dir = Path("/tmp/test_ws")
        ws = Workspace(name="test", ws_dir=ws_dir, project_path="/project")
        
        assert ws.name == "test"
        assert ws.ws_dir == ws_dir
        assert ws.project_path == "/project"
        assert ws.memory_dir == ws_dir / "memory_data"
        assert ws.sessions_dir == ws_dir / "memory_data" / "sessions"
        assert ws.team_dir == ws_dir / ".team"
        assert ws.history_db_path == ws_dir / "memory_data" / "history.db"
    
    def test_update_project_path(self):
        """更新项目路径"""
        with tempfile.TemporaryDirectory() as td:
            ws_dir = Path(td) / "test_ws"
            ws_dir.mkdir()
            
            ws = Workspace(name="test", ws_dir=ws_dir, project_path="")
            ws.update_project_path("/new/project")
            
            assert ws.project_path == "/new/project"
            
            # 验证持久化
            meta_path = ws_dir / "workspace.yaml"
            assert meta_path.exists()


class TestWorkspaceManager:
    """测试 WorkspaceManager 类"""
    
    def test_ensure_default_workspace(self):
        """默认工作空间自动创建"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=True)
            
            default = manager.get("default")
            assert default is not None
            assert default.name == "default"
    
    def test_create_workspace(self):
        """创建新工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            result = manager.create("my-project", "/path/to/project")
            
            assert "已创建" in result
            ws = manager.get("my-project")
            assert ws is not None
            assert ws.project_path == "/path/to/project"
    
    def test_create_workspace_invalid_name(self):
        """无效名称应被拒绝"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            # 空名称
            result = manager.create("")
            assert "Error" in result
            
            # 包含路径分隔符
            result = manager.create("path/with/slash")
            assert "Error" in result
            
            result = manager.create("path\\with\\backslash")
            assert "Error" in result
    
    def test_create_duplicate_workspace(self):
        """重复名称应被拒绝"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            manager.create("test")
            result = manager.create("test")
            
            assert "已存在" in result
    
    def test_add_workspace_from_path(self):
        """从路径添加工作空间"""
        with tempfile.TemporaryDirectory() as td:
            # 创建项目目录
            project_dir = Path(td) / "my_project"
            project_dir.mkdir()
            
            manager = WorkspaceManager(Path(td) / "ws_data", ensure_default=False)
            result = manager.add(str(project_dir))
            
            assert "已添加" in result
            ws = manager.get("my_project")
            assert ws is not None
    
    def test_add_nonexistent_path(self):
        """添加不存在的路径应失败"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            result = manager.add("/nonexistent/path")
            assert "Error" in result
            assert "不存在" in result
    
    def test_add_file_path(self):
        """添加文件路径（非目录）应失败"""
        with tempfile.TemporaryDirectory() as td:
            file_path = Path(td) / "file.txt"
            file_path.write_text("test")
            
            manager = WorkspaceManager(Path(td) / "ws", ensure_default=False)
            result = manager.add(str(file_path))
            
            assert "Error" in result
            assert "不是目录" in result
    
    def test_remove_workspace(self):
        """移除工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("test")
            
            result = manager.remove("test")
            
            assert "已移除" in result
            assert manager.get("test") is None
    
    def test_remove_default_workspace(self):
        """不能移除默认工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=True)
            
            result = manager.remove("default")
            
            assert "Error" in result
            assert "不能移除默认" in result
    
    def test_remove_nonexistent_workspace(self):
        """移除不存在的工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            result = manager.remove("nonexistent")
            
            assert "Error" in result
            assert "不存在" in result
    
    def test_delete_workspace(self):
        """彻底删除工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("test")
            
            result = manager.delete("test")
            
            assert "已删除" in result
            assert manager.get("test") is None
    
    def test_delete_default_workspace(self):
        """不能删除默认工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=True)
            
            result = manager.delete("default")
            
            assert "Error" in result
    
    def test_restore_workspace(self):
        """恢复已移除的工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("test")
            
            # 移除
            manager.remove("test")
            
            # 恢复
            result = manager.restore("test")
            
            assert "已恢复" in result
            assert manager.get("test") is not None
    
    def test_restore_nonexistent_workspace(self):
        """恢复未移除的工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            result = manager.restore("nonexistent")
            
            assert "Error" in result
            assert "未找到" in result
    
    def test_list_all_workspaces(self):
        """列出所有工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("ws1")
            manager.create("ws2")
            
            workspaces = manager.list_all()
            
            names = [ws["name"] for ws in workspaces]
            assert "ws1" in names
            assert "ws2" in names
    
    def test_list_removed_workspaces(self):
        """列出已移除的工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("test")
            manager.remove("test")
            
            removed = manager.list_removed()
            
            assert len(removed) == 1
            assert removed[0]["name"] == "test"
    
    def test_delete_removed_workspace(self):
        """彻底删除已移除的工作空间"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("test")
            manager.remove("test")
            
            result = manager.delete_removed("test")
            
            assert "已彻底删除" in result
            assert len(manager.list_removed()) == 0
    
    def test_render_list(self):
        """渲染工作空间列表"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("ws1", "/path1")
            manager.create("ws2")
            
            output = manager.render_list()
            
            assert "工作空间列表" in output
            assert "ws1" in output
            assert "ws2" in output
            assert "/path1" in output


class TestWorkspaceIsolation:
    """测试工作空间隔离"""
    
    def test_memory_isolation(self):
        """不同工作空间的记忆隔离"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            manager.create("ws1")
            manager.create("ws2")
            
            ws1 = manager.get("ws1")
            ws2 = manager.get("ws2")
            
            assert ws1.memory_dir != ws2.memory_dir
            assert ws1.sessions_dir != ws2.sessions_dir
            assert ws1.history_db_path != ws2.history_db_path
    
    def test_get_nonexistent_workspace(self):
        """获取不存在的工作空间返回 None"""
        with tempfile.TemporaryDirectory() as td:
            manager = WorkspaceManager(Path(td), ensure_default=False)
            
            ws = manager.get("nonexistent")
            assert ws is None
