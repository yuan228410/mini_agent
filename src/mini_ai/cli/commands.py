"""斜杠命令处理"""
from datetime import datetime, timezone, timedelta

from ..logger import logger
from ..utils import now_ts


class CommandHandler:
    def __init__(self, *, disp, store, sessions, compactor, inject_fn, run_tool_fn, lead_tools, ctx=None, workspace_mgr=None, history_db=None, context_builder=None, skill_loader=None, project_path=""):
        self.disp = disp
        self.store = store
        self.sessions = sessions
        self.compactor = compactor
        self.inject_fn = inject_fn
        self.run_tool_fn = run_tool_fn
        self.lead_tools = lead_tools
        self.ctx = ctx
        self.workspace_mgr = workspace_mgr
        self.history_db = history_db
        self.context_builder = context_builder
        self.skill_loader = skill_loader
        self.project_path = project_path
        self.plan_mode = False

    def handle(self, user_input: str, messages: list[dict]) -> str | None:
        """处理斜杠命令，返回 'continue' / 'break' / None（非命令）"""
        if user_input != "/purge":
            self._purge_pending = False
        if user_input.lower() in ("exit", "quit", "q", "/exit", "/quit", "/q"):
            return "break"

        if user_input == "/save":
            self.disp.error("用法: /save <会话名称>")
            return "continue"

        if user_input.startswith("/save "):
            self.disp.info(self.sessions.save(user_input[6:], messages))
            return "continue"

        if user_input == "/load":
            self.disp.error("用法: /load <会话名称>")
            return "continue"

        if user_input.startswith("/load "):
            loaded = self.sessions.load(user_input[6:])
            if loaded:
                messages[:] = [messages[0]] + loaded
                self.inject_fn(messages)
                self.disp.info(f"会话 '{user_input[6:]}' 已加载（{len(loaded)} 条消息）")
            else:
                self.disp.error(f"会话 '{user_input[6:]}' 不存在")
            return "continue"

        if user_input == "/sessions":
            self.disp.info(self.sessions.render_list())
            return "continue"

        if user_input == "/thinking":
            self.disp.show_thinking()
            return "continue"

        if user_input.startswith("/thinking "):
            self.disp.set_thinking_mode(user_input[10:].strip())
            return "continue"

        if user_input == "/compact":
            non_system = [m for m in messages if m["role"] != "system"]
            if len(non_system) <= self.compactor.keep_recent:
                self.disp.info(f"消息数({len(non_system)})未超过保留阈值({self.compactor.keep_recent})，无需压缩")
                return "continue"
            before = len(non_system)
            from ..llm import chat
            messages[:] = self.compactor.compact(chat, messages, ctx=self.ctx, inject_fn=self.inject_fn)
            after = len([m for m in messages if m["role"] != "system"])
            self.disp.info(f"压缩完成：{before} → {after} 条消息（摘要 {before - after} 条，历史保留在 DB 中）")
            return "continue"

        if user_input == "/history":
            history = self.history_db.load_all(limit=50)
            if not history:
                self.disp.info("暂无历史消息")
                return "continue"
            for i, msg in enumerate(history, 1):
                role = msg.get("role", "?")
                text = (msg.get("content") or "")[:100]
                ts = msg.get("timestamp", "")
                ts_display = f" {ts[2:].replace(chr(84), chr(32))}" if ts else ""
                self.disp.info(f"  [{i}] {role}{ts_display}: {text}")
            return "continue"

        if user_input == "/export" or user_input.startswith("/export "):
            parts = user_input[8:].strip().split()
            path = ""
            include_thinking = False
            include_tools = False
            for p in parts:
                if p == "--thinking":
                    include_thinking = True
                elif p == "--tools":
                    include_tools = True
                elif not path:
                    path = p
            if not path:
                from datetime import datetime as _dt
                path = f"chat_export_{_dt.now().strftime('%Y%m%d_%H%M%S')}.md"
            msgs = self.history_db.load_all()
            if not msgs:
                self.disp.info("没有历史消息可导出")
                return "continue"
            lines = ["# 对话导出\n"]
            for m in msgs:
                role = m.get("role", "")
                content = m.get("content") or ""
                ts = m.get("timestamp", "")
                if role in ("system", "tool"):
                    continue
                if role == "user":
                    label = "**🧑 用户**"
                    if ts: label += f"  `{ts}`"
                    lines.append(f"\n{label}\n\n{content}\n")
                elif role == "assistant":
                    has_thinking = include_thinking and m.get("thinking")
                    has_tools = include_tools and m.get("tool_calls")
                    if not content and not has_thinking and not has_tools:
                        continue
                    label = "**🤖 助手**"
                    if ts: label += f"  `{ts}`"
                    lines.append(f"\n{label}\n")
                    if has_thinking:
                        thinking = m["thinking"] if isinstance(m["thinking"], str) else str(m["thinking"])
                        lines.append(f"\n<details>\n<summary>💭 思考过程</summary>\n\n{thinking}\n\n</details>\n")
                    if has_tools:
                        for tc in m["tool_calls"]:
                            fn = tc.get("function", {})
                            name = fn.get("name", "?")
                            a = str(fn.get("arguments", ""))
                            result = tc.get("_result", "")
                            lines.append(f"\n> 🔧 **{name}**({a[:200]})\n")
                            if result:
                                lines.append(f"> 结果: {result[:500]}\n")
                    if content:
                        lines.append(f"\n{content}\n")
            from pathlib import Path as _P
            fp = _P(path)
            fp.write_text("\n".join(lines), encoding="utf-8")
            self.disp.info(f"已导出到 {fp.resolve()}")
            return "continue"

        if user_input == "/model":
            from ..config import AVAILABLE_MODELS, MODEL_CONFIG
            current_model = MODEL_CONFIG.get("model", "?")
            self.disp.info(f"当前: {current_model}")
            for name in AVAILABLE_MODELS:
                self.disp.info(f"  {name}")
            return "continue"

        if user_input.startswith("/model "):
            model_name = user_input[7:].strip()
            if not model_name:
                self.disp.error("用法: /model <模型名称>")
                return "continue"
            from ..config import AVAILABLE_MODELS, switch_model, MODEL_CONFIG
            if model_name not in AVAILABLE_MODELS:
                self.disp.error(f"未知模型: {model_name}，可选: {', '.join(AVAILABLE_MODELS)}")
                return "continue"
            err = switch_model(model_name)
            if err:
                self.disp.error(err)
                return "continue"
            self.disp.info(f"已切换到模型: {model_name} ({MODEL_CONFIG.get('model', '?')})")
            return "continue"

        if user_input == "/purge":
            if not getattr(self, '_purge_pending', False):
                self._purge_pending = True
                self.disp.info("⚠ 确认要彻底删除所有历史消息（不可恢复）？再次输入 /purge 确认")
                return "continue"
            self._purge_pending = False
            non_system = [m for m in messages if m["role"] != "system"]
            if not non_system:
                self.disp.info("没有历史消息需要删除")
                return "continue"
            count = len(non_system)
            messages[:] = [messages[0]]
            self.inject_fn(messages)
            self.history_db.purge()
            self.disp.info(f"已彻底删除 {count} 条历史消息（不可恢复）")
            return "continue"

        if user_input == "/clear":
            non_system = [m for m in messages if m["role"] != "system"]
            if not non_system:
                self.disp.info("没有会话消息需要清空")
                return "continue"
            messages[:] = [messages[0]]
            self.inject_fn(messages)
            self.disp.info(f"已清空 {len(non_system)} 条上下文消息（历史保留在 DB 中）")
            return "continue"

        if user_input == "/prompt":
            if not self.context_builder:
                self.disp.error("context_builder 不可用")
                return "continue"
            prompt = self.context_builder.build(
                memory_store=self.store, skill_loader=self.skill_loader,
                project_path=self.project_path,
            )
            self.disp.info(f"系统提示词（{len(prompt)} 字符）：\n{prompt}")
            return "continue"

        if user_input == "/genskill":
            self.disp.error("用法: /genskill <技能名称>")
            return "continue"

        if user_input.startswith("/genskill "):
            skill_name = user_input[10:].strip()
            if not skill_name:
                self.disp.error("用法: /genskill <技能名称>")
                return "continue"
            prompt = f"请将当前对话中的关键方法论、步骤和经验总结为一个名为 '{skill_name}' 的技能。要求：1) 用 YAML frontmatter 定义 name 和 description；2) 正文用 Markdown 格式，结构清晰，步骤明确；3) 调用 install_skill 工具安装，使用 content 参数传入技能内容。"
            messages.append({"role": "user", "content": prompt, "timestamp": now_ts()})
            self.history_db.append("user", prompt)
            msg = self.run_tool_fn(messages, self.lead_tools, self.inject_fn, self.disp, ctx=self.ctx)
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
                self.history_db.append("assistant", msg["content"])
            return "continue"

        if user_input == "/skill":
            from ..tools import dispatch
            self.disp.info(dispatch("list_skills", {}))
            return "continue"

        if user_input.startswith("/skill ") and user_input[7:].strip() == "":
            self.disp.error("用法: /skill <技能名称>")
            return "continue"

        if user_input.startswith("/skill "):
            skill_name = user_input[7:].strip()
            if not skill_name:
                self.disp.error("用法: /skill <技能名称>")
                return "continue"
            prompt = f"请加载并使用技能 '{skill_name}' 来完成用户后续的任务。先调用 load_skill 了解该技能的详细内容和使用方式，然后严格按照技能指引执行。"
            messages.append({"role": "user", "content": prompt, "timestamp": now_ts()})
            self.history_db.append("user", prompt)
            msg = self.run_tool_fn(messages, self.lead_tools, self.inject_fn, self.disp, ctx=self.ctx)
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
                self.history_db.append("assistant", msg["content"])
            return "continue"

        if user_input == "/workspace":
            if self.workspace_mgr:
                self.disp.info(self.workspace_mgr.render_list())
            else:
                self.disp.error("工作空间功能未启用")
            return "continue"

        if user_input.startswith("/workspace "):
            if not self.workspace_mgr:
                self.disp.error("工作空间功能未启用")
                return "continue"
            sub = user_input[11:].strip()
            if sub.startswith("new "):
                parts = sub[4:].strip().split(None, 1)
                name = parts[0] if parts else ""
                path = parts[1] if len(parts) > 1 else ""
                self.disp.info(self.workspace_mgr.create(name, path))
            elif sub.startswith("add "):
                self.disp.info(self.workspace_mgr.add(sub[4:].strip()))
            elif sub.startswith("remove "):
                self.disp.info(self.workspace_mgr.remove(sub[7:].strip()))
            elif sub.startswith("delete "):
                self.disp.info(self.workspace_mgr.delete(sub[7:].strip()))
            else:
                ws = self.workspace_mgr.get(sub)
                if not ws:
                    self.disp.error(f"工作空间 '{sub}' 不存在")
                else:
                    from ..config import _raw, _config_path
                    import yaml as _yaml
                    _raw["active_workspace"] = sub
                    _config_path.write_text(_yaml.dump(_raw, default_flow_style=False, allow_unicode=True), encoding="utf-8")
                    self.disp.info(f"已切换到工作空间 '{sub}'，正在重新加载...")
                    return "reload_workspace"
            return "continue"

        if user_input == "/plan":
            self.plan_mode = True
            self.disp.info("已进入计划模式 📋 — 后续消息只规划不执行，输入 /act 开始执行")
            return "continue"

        if user_input == "/act":
            if not self.plan_mode:
                self.disp.info("当前已是执行模式")
                return "continue"
            self.plan_mode = False
            self.disp.info("已切换到执行模式 ⚡")
            return "continue"

        if user_input == "/mcp":
            from ..tools.mcp_loader import _MCP_ENABLED, _MCP_SERVERS
            if not _MCP_ENABLED:
                self.disp.info("MCP 未启用 (config.yaml → mcp.enabled: true)")
                return "continue"
            if not _MCP_SERVERS:
                self.disp.info("MCP 已启用但未配置服务器 (config.yaml → mcp.servers)")
                return "continue"
            from ..main import get_app_context
            mcp_loader = get_app_context().mcp_loader
            if not mcp_loader:
                self.disp.info("MCP Loader 未初始化")
                return "continue"
            conns = mcp_loader._connections
            if not conns:
                self.disp.info("MCP 无已连接服务器")
                return "continue"
            lines = []
            for name, conn in conns.items():
                tool_names = [t.name for t in conn.tools]
                lines.append(f"  {name} ({conn.conn_type}): {len(tool_names)} 工具 — {', '.join(tool_names)}")
            info_text = f"MCP 服务器 ({len(conns)}):" + "\n" + "\n".join(lines)
            self.disp.info(info_text)
            return "continue"

        if user_input == "/todos":
            from ..tools import render_todos
            text = render_todos()
            if not text:
                self.disp.info("暂无任务计划")
                return "continue"
            from rich.rule import Rule
            from rich.text import Text
            self.disp.console.print()
            self.disp.console.print(Rule("📋 任务计划", style="amber", characters="─"))
            for line in text.split("\n"):
                if not line.strip() or line.startswith("📋"):
                    continue
                if "← 当前" in line:
                    self.disp.console.print(Text(f"  {line}", style="bold yellow"))
                elif line.strip().startswith("[x]"):
                    self.disp.console.print(Text(f"  {line}", style="dim"))
                else:
                    self.disp.console.print(Text(f"  {line}"))
            return "continue"

        return None
