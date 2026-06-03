"""斜杠命令处理"""
from datetime import datetime, timezone, timedelta

from ..logger import logger
from ..utils import now_ts


class CommandHandler:
    def __init__(self, *, disp, store, compactor, inject_fn, run_tool_fn, lead_tools, ctx=None, workspace_mgr=None, history_db=None, context_builder=None, skill_loader=None, project_path="", username="default", session_id=None):
        self.disp = disp
        self.store = store
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
        self.username = username
        self.session_id = session_id
        self.plan_mode = False

    def handle(self, user_input: str, messages: list[dict]) -> str | None:
        """处理斜杠命令，返回 'continue' / 'break' / None（非命令）"""
        return self.handle_commands(user_input, messages)
    
    def _get_workspace(self) -> str:
        """获取当前工作空间名称"""
        if self.workspace_mgr:
            # 从 project_path 获取工作空间名称
            cwd = self.project_path or "."
            from pathlib import Path
            ws_name = Path(cwd).name if cwd else "default"
            # 验证工作空间是否存在
            ws = self.workspace_mgr.get(ws_name)
            if ws:
                return ws.name
        return "default"
    
    def handle_commands(self, user_input: str, messages: list[dict]) -> str | None:
        """处理斜杠命令，返回 'continue' / 'break' / None（非命令）"""
        if user_input != "/purge":
            self._purge_pending = False
        if user_input.lower() in ("exit", "quit", "q", "/exit", "/quit", "/q"):
            return "break"

        if user_input == "/sessions":
            # 列出数据库中的所有会话
            sessions = self.history_db.list_sessions(self._get_workspace())
            if not sessions:
                self.disp.info("暂无会话记录")
                return "continue"
            
            lines = [f"共 {len(sessions)} 个会话：\n"]
            for s in sessions:
                current = " (当前)" if s["session_id"] == self.session_id else ""
                name = self.history_db.get_session_name(self._get_workspace(), s["session_id"]) or s["session_id"][:15]
                ts = s.get("updated_at", "")[:16].replace("T", " ")
                lines.append(f"  {'*' if current else ' '} {name:20s} {s['message_count']:>4} 条  {ts}{current}")
            self.disp.info("\n".join(lines))
            return "continue"

        if user_input == "/session":
            # 显示当前会话信息
            name = self.history_db.get_session_name(self._get_workspace(), self.session_id)
            self.disp.info(f"当前会话: {name or self.session_id}\nID: {self.session_id}\n用户: {self.username}")
            return "continue"

        if user_input.startswith("/switch "):
            # 切换到指定会话
            sid = user_input[8:].strip()
            if not sid:
                self.disp.error("用法: /switch <会话ID或名称>")
                return "continue"
            
            # 尝试按名称查找
            sessions = self.history_db.list_sessions(self._get_workspace())
            matched = None
            for s in sessions:
                sname = self.history_db.get_session_name(self._get_workspace(), s["session_id"])
                if s["session_id"] == sid or sname == sid:
                    matched = s["session_id"]
                    break
            
            if not matched:
                self.disp.error(f"会话 '{sid}' 不存在")
                return "continue"
            
            # 重新加载会话
            self.session_id = matched
            messages[:] = [messages[0]]  # 保留 system
            restored = self.history_db.load_session(self._get_workspace(), matched, limit=50)
            if restored:
                messages.extend(restored)
            self.inject_fn(messages)
            self.disp.info(f"已切换到会话: {matched}")
            return "continue"

        if user_input == "/new":
            # 创建新会话
            import uuid
            old_sid = self.session_id
            self.session_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:8]
            messages[:] = [messages[0]]  # 保留 system
            self.inject_fn(messages)
            self.disp.info(f"已创建新会话: {self.session_id}")
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
            workspace = self._get_workspace()
            history = self.history_db.load_session(workspace, self.session_id, limit=50)
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
            workspace = self._get_workspace()
            msgs = self.history_db.load_session(workspace, self.session_id)
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
            from ..llm import estimate_tokens
            prompt = self.context_builder.build(
                memory_store=self.store, skill_loader=self.skill_loader,
                project_path=self.project_path,
            )
            chars = len(prompt)
            tokens = estimate_tokens(prompt)
            self.disp.info(f"系统提示词（{chars} 字符, ~{tokens} tokens）：\n{prompt}")
            return "continue"
        
        if user_input == "/tools":
            from ..tools import get_definitions
            from ..llm import estimate_tokens
            import json
            
            tool_defs = get_definitions()
            if not tool_defs:
                self.disp.info("暂无可用工具")
                return "continue"
            
            tool_count = len(tool_defs)
            tool_json = json.dumps(tool_defs, ensure_ascii=False, indent=2)
            chars = len(tool_json)
            tokens = estimate_tokens(tool_json)
            
            # 打印工具列表
            tool_names = [d["function"]["name"] for d in tool_defs]
            self.disp.info(f"工具定义（{tool_count} 个工具, {chars} 字符, ~{tokens} tokens）：")
            self.disp.info(f"工具列表: {', '.join(tool_names)}\n")
            self.disp.info(tool_json)
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

        # ── 技能管理命令 ──
        
        if user_input == "/skills":
            # 列出所有技能
            if not self.skill_loader or not self.skill_loader.skills:
                self.disp.info("暂无已安装的技能")
                self.disp.info("\n使用以下命令安装:")
                self.disp.info("  /skill install <url|path>")
                return "continue"
            
            from rich.table import Table
            
            table = Table(title="📚 可用技能", show_header=True, header_style="bold cyan")
            table.add_column("名称", style="cyan")
            table.add_column("描述", style="white")
            table.add_column("层级", style="magenta")
            
            for name, skill in self.skill_loader.skills.items():
                desc = skill["meta"].get("description", "无描述")
                tier = skill.get("tier", "global")
                
                table.add_row(
                    name,
                    desc[:50] + ("..." if len(desc) > 50 else ""),
                    tier,
                )
            
            self.disp.console.print(table)
            self.disp.info("\n使用 /skill load <name> 加载技能")
            return "continue"
        
        if user_input == "/skill":
            # 显示技能帮助
            from rich.panel import Panel
            self.disp.console.print(Panel(
                """
[bold]技能管理命令:[/bold]

  [cyan]/skills[/cyan]                     列出所有技能
  [cyan]/skill install <url|path>[/cyan]    安装技能
  [cyan]/skill uninstall <name>[/cyan]      卸载技能
  [cyan]/skill load <name>[/cyan]           加载技能
  [cyan]/skill info <name>[/cyan]           查看技能详情
  [cyan]/skill create <name>[/cyan]         创建技能模板

[bold]安装选项:[/bold]

  --global     安装到全局（默认）
  --user       安装到用户级
  --workspace  安装到工作空间

[bold]使用示例:[/bold]

  [yellow]安装技能[/yellow]
  /skill install https://.../skill.md
  /skill install /path/to/skill-dir --workspace

  [yellow]查看技能[/yellow]
  /skill info enterprise-search

  [yellow]加载技能[/yellow]
  /skill load python-expert
  或让模型自动调用: load_skill("python-expert")

  [yellow]创建技能[/yellow]
  /skill create my-workflow --workspace
        """,
                title="📖 技能帮助",
                border_style="blue",
            ))
            return "continue"
        
        if user_input.startswith("/skill "):
            # 解析技能子命令
            parts = user_input[7:].strip().split(maxsplit=1)
            if not parts:
                self.disp.error("用法: /skill <install|uninstall|load|info|create> ...")
                return "continue"
            
            action = parts[0]
            params = parts[1] if len(parts) > 1 else ""
            
            if action == "install":
                # /skill install <url|path> [--global|--user|--workspace]
                if not params:
                    self.disp.error("用法: /skill install <url|path> [--global|--user|--workspace]")
                    return "continue"
                
                # 解析层级
                level = "global"
                if "--user" in params:
                    level = "user"
                    params = params.replace("--user", "").strip()
                elif "--workspace" in params:
                    level = "workspace"
                    params = params.replace("--workspace", "").strip()
                
                params = params.replace("--global", "").strip()
                
                if not params:
                    self.disp.error("请指定技能 URL 或路径")
                    return "continue"
                
                # 调用 install_skill 工具
                from ..tools import dispatch
                if params.startswith("http"):
                    result = dispatch("install_skill", {"source": params, "level": level})
                else:
                    result = dispatch("install_skill", {"source": params, "level": level})
                
                self.disp.info(result)
                return "continue"
            
            elif action == "uninstall":
                # /skill uninstall <name>
                if not params:
                    self.disp.error("用法: /skill uninstall <name>")
                    return "continue"
                
                name = params.split()[0]
                level = params.split()[1] if len(params.split()) > 1 else None
                
                from ..tools import dispatch
                if level:
                    result = dispatch("delete_skill", {"name": name, "level": level})
                else:
                    result = dispatch("delete_skill", {"name": name})
                
                if result.startswith("Error:"):
                    self.disp.error(result)
                else:
                    self.disp.info(result)
                return "continue"
            
            elif action == "load":
                # /skill load <name>
                if not params:
                    self.disp.error("用法: /skill load <name>")
                    return "continue"
                
                skill_name = params.split()[0]
                from ..tools import dispatch
                result = dispatch("load_skill", {"name": skill_name})
                
                if result.startswith("Error:"):
                    self.disp.error(result)
                else:
                    self.disp.info(f"✓ 已加载技能: {skill_name}")
                return "continue"
            
            elif action == "info":
                # /skill info <name>
                if not params:
                    self.disp.error("用法: /skill info <name>")
                    return "continue"
                
                skill_name = params.split()[0]
                
                if not self.skill_loader or skill_name not in self.skill_loader.skills:
                    self.disp.error(f"未找到技能: {skill_name}")
                    return "continue"
                
                skill = self.skill_loader.skills[skill_name]
                meta = skill["meta"]
                
                from rich.panel import Panel
                info = f"**{skill_name}**\n\n"
                if meta.get("description"):
                    info += f"{meta['description']}\n\n"
                if meta.get("tags"):
                    info += f"标签: {meta['tags']}\n"
                info += f"层级: {skill.get('tier', 'global')}\n"
                info += f"路径: {skill['path']}\n"
                
                self.disp.console.print(Panel(info, title=f"📖 {skill_name}", border_style="blue"))
                return "continue"
            
            elif action == "create":
                # /skill create <name> [--global|--user|--workspace]
                if not params:
                    self.disp.error("用法: /skill create <name> [--global|--user|--workspace]")
                    return "continue"
                
                # 解析层级
                level = "global"
                if "--user" in params:
                    level = "user"
                    params = params.replace("--user", "").strip()
                elif "--workspace" in params:
                    level = "workspace"
                    params = params.replace("--workspace", "").strip()
                
                params = params.replace("--global", "").strip()
                
                if not params:
                    self.disp.error("请指定技能名称")
                    return "continue"
                
                skill_name = params.split()[0]
                
                # 创建技能模板
                if not self.skill_loader:
                    self.disp.error("技能加载器不可用")
                    return "continue"
                
                target_dir = self.skill_loader.get_tier_dir(level)
                if not target_dir:
                    self.disp.error(f"层级 '{level}' 未配置")
                    return "continue"
                
                skill_dir = target_dir / skill_name
                if skill_dir.exists():
                    self.disp.error(f"技能 '{skill_name}' 已存在于 {level} 层级")
                    return "continue"
                
                # 创建目录结构
                skill_dir.mkdir(parents=True, exist_ok=True)
                skill_file = skill_dir / "SKILL.md"
                
                # 写入模板内容
                template = f"""---
name: {skill_name}
description: 技能描述（请修改）
tags: 标签1,标签2
---

# {skill_name}

技能内容（请修改）

## 使用场景

- 场景1
- 场景2

## 步骤

1. 步骤1
2. 步骤2
"""
                skill_file.write_text(template, encoding="utf-8")
                
                # 刷新技能列表
                self.skill_loader._load_all()
                
                self.disp.info(f"✓ 已创建技能模板: {skill_name}")
                self.disp.info(f"  目录: {skill_dir}")
                self.disp.info(f"  请编辑 SKILL.md 添加技能内容")
                return "continue"
            
            else:
                self.disp.error(f"未知子命令: {action}")
                self.disp.info("可用子命令: install, uninstall, load, info, create")
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
