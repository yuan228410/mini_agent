"""工作空间管理 — 按项目隔离记忆、会话、历史"""
import shutil
from pathlib import Path

import yaml

from .logger import logger


class Workspace:

    def __init__(self, name: str, ws_dir: Path, project_path: str = ""):
        self.name = name
        self.ws_dir = ws_dir
        self.project_path = project_path
        self.memory_dir = ws_dir / "memory_data"
        self.sessions_dir = ws_dir / "memory_data" / "sessions"
        self.team_dir = ws_dir / ".team"
        self.history_db_path = ws_dir / "memory_data" / "history.db"

    def update_project_path(self, path: str):
        self.project_path = path
        meta_path = self.ws_dir / "workspace.yaml"
        meta = {"name": self.name, "project_path": path}
        meta_path.write_text(
            yaml.dump(meta, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )


class WorkspaceManager:

    def __init__(self, data_dir: Path, ensure_default: bool = True):
        self.data_dir = data_dir
        self.workspaces_dir = data_dir / "workspaces"
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        if ensure_default:
            self._ensure_default()

    def _ensure_default(self):
        default_dir = self.workspaces_dir / "default"
        if not default_dir.exists():
            self._create_workspace_dir("default")

    def _create_workspace_dir(self, name: str, project_path: str = "") -> Path:
        ws_dir = self.workspaces_dir / name
        ws_dir.mkdir(parents=True, exist_ok=True)
        (ws_dir / "memory_data").mkdir(exist_ok=True)
        (ws_dir / "memory_data" / "sessions").mkdir(exist_ok=True)
        (ws_dir / ".team" / "inbox").mkdir(parents=True, exist_ok=True)

        meta = {"name": name, "project_path": project_path}
        (ws_dir / "workspace.yaml").write_text(
            yaml.dump(meta, default_flow_style=False, allow_unicode=True),
            encoding="utf-8",
        )
        logger.info(f"[Workspace] 创建 '{name}' path={project_path}")
        return ws_dir

    def create(self, name: str, project_path: str = "") -> str:
        if not name or "/" in name or "\\" in name:
            return "Error: 工作空间名称不能包含路径分隔符"
        ws_dir = self.workspaces_dir / name
        if ws_dir.exists():
            return f"Error: 工作空间 '{name}' 已存在"
        self._create_workspace_dir(name, project_path)
        return f"已创建工作空间 '{name}'"

    def add(self, project_path: str) -> str:
        path = Path(project_path).expanduser().resolve()
        if not path.exists():
            return f"Error: 路径不存在: {path}"
        if not path.is_dir():
            return f"Error: 不是目录: {path}"
        name = path.name
        ws_dir = self.workspaces_dir / name
        if ws_dir.exists():
            return f"Error: 工作空间 '{name}' 已存在"
        self._create_workspace_dir(name, str(path))
        return f"已添加工作空间 '{name}' → {path}"

    def remove(self, name: str) -> str:
        if name == "default":
            return "Error: 不能移除默认工作空间"
        ws_dir = self.workspaces_dir / name
        if not ws_dir.exists():
            return f"Error: 工作空间 '{name}' 不存在"
        backup = ws_dir.with_name(f".{name}.removed")
        if backup.exists():
            shutil.rmtree(backup)
        ws_dir.rename(backup)
        logger.info(f"[Workspace] 移除 '{name}'（数据备份到 {backup}）")
        return f"已移除工作空间 '{name}'（数据备份到 {backup.name}）"

    def delete(self, name: str) -> str:
        if name == "default":
            return "Error: 不能删除默认工作空间"
        ws_dir = self.workspaces_dir / name
        if not ws_dir.exists():
            return f"Error: 工作空间 '{name}' 不存在"
        shutil.rmtree(ws_dir)
        logger.info(f"[Workspace] 删除 '{name}'（含所有数据）")
        return f"已删除工作空间 '{name}'（所有数据已清除）"

    def get(self, name: str) -> Workspace | None:
        ws_dir = self.workspaces_dir / name
        if not ws_dir.exists():
            return None
        meta_path = ws_dir / "workspace.yaml"
        project_path = ""
        if meta_path.exists():
            try:
                meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                project_path = meta.get("project_path", "")
            except Exception:
                pass
        return Workspace(name=name, ws_dir=ws_dir, project_path=project_path)

    def list_all(self) -> list[dict]:
        result = []
        for d in sorted(self.workspaces_dir.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith(".") or d.name.endswith(".removed"):
                continue
            meta_path = d / "workspace.yaml"
            project_path = ""
            if meta_path.exists():
                try:
                    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                    project_path = meta.get("project_path", "")
                except Exception:
                    pass
            result.append({"name": d.name, "project_path": project_path})
        return result

    def render_list(self) -> str:
        workspaces = self.list_all()
        if not workspaces:
            return "无工作空间"
        lines = ["工作空间列表:"]
        for ws in workspaces:
            path_info = f" → {ws['project_path']}" if ws["project_path"] else ""
            lines.append(f"  - {ws['name']}{path_info}")
        return "\n".join(lines)
