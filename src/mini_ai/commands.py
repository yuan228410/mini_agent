"""斜杠命令处理"""
from .logger import logger


class CommandHandler:
    def __init__(self, *, disp, store, sessions, compactor, inject_fn, run_tool_fn, lead_tools):
        self.disp = disp
        self.store = store
        self.sessions = sessions
        self.compactor = compactor
        self.inject_fn = inject_fn
        self.run_tool_fn = run_tool_fn
        self.lead_tools = lead_tools

    def handle(self, user_input: str, messages: list[dict]) -> str | None:
        """处理斜杠命令，返回 'continue' / 'break' / None（非命令）"""
        if user_input.lower() in ("exit", "quit", "q"):
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
            from .llm import chat
            messages[:] = self.compactor.compact(chat, messages)
            self.inject_fn(messages)
            after = len([m for m in messages if m["role"] != "system"])
            self.disp.info(f"压缩完成：{before} → {after} 条消息（归档 {before - after} 条）")
            return "continue"

        if user_input == "/history":
            unarchived = self.store.load_unarchived()
            if not unarchived:
                self.disp.info("暂无历史消息")
                return "continue"
            for i, msg in enumerate(unarchived, 1):
                role = msg.get("role", "?")
                text = (msg.get("content") or "")[:100]
                self.disp.info(f"  [{i}] {role}: {text}")
            return "continue"

        if user_input == "/model":
            from .config import AVAILABLE_MODELS, MODEL_CONFIG
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
            from .config import AVAILABLE_MODELS, switch_model, MODEL_CONFIG
            if model_name not in AVAILABLE_MODELS:
                self.disp.error(f"未知模型: {model_name}，可选: {', '.join(AVAILABLE_MODELS)}")
                return "continue"
            err = switch_model(model_name)
            if err:
                self.disp.error(err)
                return "continue"
            self.disp.info(f"已切换到模型: {model_name} ({MODEL_CONFIG.get('model', '?')})")
            return "continue"

        if user_input == "/clear":
            non_system = [m for m in messages if m["role"] != "system"]
            if not non_system:
                self.disp.info("没有会话消息需要清空")
                return "continue"
            messages[:] = [messages[0]]
            self.inject_fn(messages)
            self.store.clear_history()
            self.disp.info(f"已清空 {len(non_system)} 条会话消息")
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
            messages.append({"role": "user", "content": prompt})
            self.store.append("user", prompt)
            msg = self.run_tool_fn(messages, self.lead_tools, self.inject_fn, self.disp)
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
                self.store.append("assistant", msg["content"])
            return "continue"

        if user_input == "/skill":
            from .tools import dispatch
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
            messages.append({"role": "user", "content": prompt})
            self.store.append("user", prompt)
            msg = self.run_tool_fn(messages, self.lead_tools, self.inject_fn, self.disp)
            if msg and msg.get("content"):
                messages.append({"role": "assistant", "content": msg["content"]})
                self.store.append("assistant", msg["content"])
            return "continue"

        return None
