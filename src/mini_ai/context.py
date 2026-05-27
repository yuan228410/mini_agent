"""上下文组装器：从多个来源构建系统提示词"""
from pathlib import Path


class ContextBuilder:
    """按优先级组装系统提示词。

    组装顺序：
      1. 核心身份 (SOUL.md)
      2. 长期记忆 (MemoryStore)
      3. 用户画像 (MemoryStore)
      4. 技能列表 (SkillLoader)
      5. 项目规范 (CLAUDE.md / AGENTS.md)
      6. 系统指令 (SYSTEM_PROMPT 模板)
    """

    def __init__(self, project_dir: Path):
        self.project_dir = Path(project_dir)
        from .config import PACKAGE_DIR
        self.character_dir = PACKAGE_DIR / "character"

    def build(self, memory_store=None, skill_loader=None, project_path: str = "") -> str:
        parts = []

        soul = self._read_doc("SOUL.md")
        if soul:
            parts.append(soul)

        if memory_store:
            if memory_store.has_memory():
                parts.append(f"## 长期记忆\n\n{memory_store.read_memory()}")

            if memory_store.has_user():
                parts.append(f"## 用户画像\n\n{memory_store.read_user()}")

        if skill_loader:
            skills_text = skill_loader.get_descriptions()
            if skills_text and skills_text != "(no skills available)":
                parts.append(f"## 可用技能\n\n{skills_text}")

        if project_path:
            parts.append("## 当前工作空间\n\n项目路径: " + project_path + "\n\n所有文件操作（读写文件、执行命令）默认在此目录下进行")

        cwd_docs = self._read_project_docs(project_path)
        if cwd_docs:
            parts.append(cwd_docs)

        rules = self._read_doc("RULES.md")
        if rules:
            parts.append(rules)

        return "\n\n---\n\n".join(parts) if parts else ""


    def _build_self_info(self) -> str | None:
        try:
            from .config import _raw, AVAILABLE_MODELS, MODEL_CONFIG, MCP, STREAMING, THINKING, PLAN, COMPACTOR, DISPLAY, DATA_DIR, PACKAGE_DIR
            from . import __version__
            from .tools import get_definitions
        except Exception:
            return None

        active = _raw.get("active_model", "?")
        model_name = MODEL_CONFIG.get("model", "?")
        ctx_len = MODEL_CONFIG.get("context_length", 128000)
        api_mode = MODEL_CONFIG.get("api_mode", "openai")

        tools = get_definitions()
        tool_names = [t["function"]["name"] for t in tools]
        mcp_tools = [n for n in tool_names if n.startswith("mcp_")]
        builtin_tools = [n for n in tool_names if not n.startswith("mcp_")]

        lines = [
            f"你是 mini-ai v{__version__}，一个智能对话 Agent。",
            f"当前模型: {active} ({model_name}, {api_mode}, context_length={ctx_len})",
            f"可用模型: {', '.join(AVAILABLE_MODELS)}",
            f"流式输出: {'是' if STREAMING else '否'}",
            f"思考模式: {'启用' if THINKING.get('enabled') else '禁用'} (budget={THINKING.get('budget_tokens', 10000)})，显示: {DISPLAY.get('thinking_mode', 'collapsed')} (collapsed/expanded/hidden)",
            f"计划模式审批: {'需要' if PLAN.get('approval', True) else '自动执行'}",
            f"压缩阈值: keep_recent={COMPACTOR.get('keep_recent', 50)}, char_threshold={COMPACTOR.get('char_threshold', 20000)}",
            f"MCP: {'启用' if MCP.get('enabled') else '禁用'} ({len(mcp_tools)} 个工具)" if MCP.get("enabled") else f"MCP: 禁用",
            f"内置工具 ({len(builtin_tools)}): {', '.join(builtin_tools)}",
        ]
        if mcp_tools:
            lines.append(f"MCP 工具 ({len(mcp_tools)}): {', '.join(mcp_tools)}")
        lines.append(f"配置文件: {DATA_DIR}/config.yaml")
        lines.append(f"源码目录: {PACKAGE_DIR}")
        lines.append(f"项目文档: {PACKAGE_DIR.parent.parent}/README.md, {PACKAGE_DIR.parent.parent}/WEB.md, {PACKAGE_DIR.parent.parent}/DESIGN.md")
        lines.append(f"配置示例文件: {PACKAGE_DIR}/config.example.yaml（包含所有配置项及详细说明，用 read_file 读取了解完整配置结构）")
        lines.append("你可以用 read_file 读取自己的源码和文档（如源码目录下的 .py 文件、README.md 等），也可以用 config 工具读取/修改配置（action=list 查看结构，action=read path=xxx 读取值，action=write path=xxx value=yyy 修改值）")

        return "## mini-ai 自身信息\n\n" + "\n".join(lines)

    def _read_project_docs(self, project_path: str = "") -> str | None:
        import os
        search_dirs = []
        if project_path:
            search_dirs.append(Path(project_path))
        search_dirs.append(Path(os.getcwd()))

        for d in search_dirs:
            for name in ("CLAUDE.md", "AGENTS.md"):
                path = d / name
                if path.exists():
                    text = path.read_text(encoding="utf-8").strip()
                    if text:
                        return f"## {name}\n\n{text}"
        return None

    def _read_doc(self, name: str) -> str | None:
        path = self.character_dir / name
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        return None