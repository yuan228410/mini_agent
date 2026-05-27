"""终端 UI 渲染层：Markdown 渲染、思维链展示、工具调用展示"""
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from ..logger import logger

_IS_TTY = sys.stdout.isatty()

_SLASH_COMMANDS = [
    ("/save", "保存当前对话为命名会话"),
    ("/load", "加载已保存的会话"),
    ("/sessions", "列出所有已保存的会话"),
    ("/compact", "手动触发对话压缩"),
    ("/clear", "清空历史消息（归档）"),
    ("/purge", "彻底删除历史消息（不可恢复）"),
    ("/model", "切换模型"),
    ("/history", "查看历史消息"),
    ("/genskill", "从对话中总结生成技能"),
    ("/skill", "使用指定技能执行任务"),
    ("/thinking", "查看最近思考过程"),
    ("/thinking collapsed", "折叠模式（仅摘要）"),
    ("/thinking expanded", "展开模式（实时显示）"),
    ("/thinking hidden", "隐藏思考过程"),
    ("/workspace", "列出所有工作空间"),
    ("/workspace new", "创建新工作空间"),
    ("/workspace add", "添加现有文件夹为工作空间"),
    ("/workspace remove", "移除工作空间（保留数据）"),
    ("/workspace delete", "删除工作空间（含数据）"),
    ("/plan", "进入计划模式（只规划不执行）"),
    ("/act", "切换到执行模式"),
    ("/mcp", "查看 MCP 服务器状态"),
    ("/exit", "退出"),
]

def _build_completions():
    items = list(_SLASH_COMMANDS)
    try:
        from ..skills import SkillLoader
        from ..config import DATA_DIR, SKILL_PATHS
        loader = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
        for name, skill in loader.skills.items():
            desc = skill["meta"].get("description", "")
            items.append((f"/skill {name}", desc))
    except Exception:
        pass
    try:
        from ..config import AVAILABLE_MODELS, MODEL_CONFIG
        for name in AVAILABLE_MODELS:
            model_id = MODEL_CONFIG.get("model", "") if name == _raw_active() else _models_raw().get(name, {}).get("model", "")
            items.append((f"/model {name}", model_id))
    except Exception:
        pass
    return items

def _raw_active():
    from ..config import _raw
    return _raw.get("active_model", "")

def _models_raw():
    from ..config import _raw
    return _raw.get("models", {})

_ALL_COMPLETIONS = _build_completions()


class Display:
    def __init__(self, thinking_mode: str = "collapsed", tool_detail: str = "summary"):
        self.console = Console()
        self.thinking_mode = thinking_mode
        self.tool_detail = tool_detail
        self._stream_buf = ""
        self._streaming = False
        self._stream_line_count = 0
        self._thinking_buf = ""
        self._thinking_start_time = 0.0
        self._tool_start_time = 0.0
        self._last_thinking = ""
        self._had_thinking = False
        self.show_banner()

    def show_banner(self):
        if not _IS_TTY:
            return
        from rich.panel import Panel
        from rich import box
        from .. import __version__
        art_lines = [
            r" __  __ _       _    ___ _ ",
            r"|  \/  (_)_ __ | |_ / __| |",
            r"| |\/| | | '_ \| __/ /  | |",
            r"| |  | | | | | | |_ / /| |",
            r"|_|  |_|_|_| |_|\__|___|_|",
        ]
        mini_len = len(r" __  __ _       _ ")
        banner = Text()
        for line in art_lines:
            if banner:
                banner.append("\n")
            banner.append(line[:mini_len], style="bold green")
            banner.append(line[mini_len:], style="bold yellow")
        subtitle = Text(f"v{__version__}  |  智能对话 Agent", style="dim")
        panel = Panel(banner, subtitle=subtitle, box=box.SIMPLE, padding=(0, 2), expand=False)
        self.console.print(panel)
        self.console.print()

    def user_input(self, plan_mode: bool = False) -> str:
        prompt_text = "mini-ai 📋> " if plan_mode else "mini-ai> "
        if _IS_TTY and sys.stdin.isatty():
            return self._prompt_input(prompt_text)
        try:
            line = input(prompt_text)
        except EOFError:
            return "exit"
        if not line and not sys.stdin.isatty():
            return "exit"
        return line

    def _prompt_input(self, prompt_text: str = "mini-ai> ") -> str:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.completion import Completer, Completion
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.styles import Style

        class SlashCompleter(Completer):
            def get_completions(self, document, complete_event):
                text = document.text_before_cursor
                if not text.startswith("/"):
                    return
                for cmd, desc in _ALL_COMPLETIONS:
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text), display_meta=desc)

        if not hasattr(self, "_session"):
            self._session = PromptSession(
                message=FormattedText([("class:prompt", "mini-ai> ")]),
                completer=SlashCompleter(),
                complete_while_typing=True,
                style=Style.from_dict({
                    "prompt": "bold cyan",
                    "completion-menu.completion.current": "bg:#005577 #ffffff",
                    "completion-menu.completion": "bg:#222222 #aaaaaa",
                    "completion-menu.meta.current": "bg:#005577 #ffff88",
                    "completion-menu.meta": "bg:#222222 #888888",
                }),
            )
        self._session.message = FormattedText([("class:prompt", prompt_text)])
        try:
            return self._session.prompt()
        except (EOFError, KeyboardInterrupt):
            return "exit"

    def text_chunk(self, text: str):
        self._stream_buf += text
        if _IS_TTY:
            if not self._streaming:
                self._stream_line_count = 0
                self._streaming = True
            lines = text.split("\n")
            self._stream_line_count += len(lines) - 1
            print(text, end="", flush=True)

    def text_end(self, full_text: str | None = None, timestamp: str = ""):
        content = full_text if full_text is not None else self._stream_buf
        if not content:
            self._reset_stream()
            return

        if self._had_thinking:
            self.console.print()
            ts = f" [{timestamp[2:].replace('T', ' ')}]" if timestamp else ""
            self.console.print(Text(f"Assistant{ts}", style="bold"))
            self._had_thinking = False

        if _IS_TTY and self._streaming:
            line_count = getattr(self, '_stream_line_count', 0)
            buf_lines = self._stream_buf.count("\n")
            total_lines = max(line_count, buf_lines) + 2
            sys.stdout.write(f"\033[{total_lines}A")
            sys.stdout.write("\033[J")
            sys.stdout.flush()

        self.console.print(Markdown(content))
        self._reset_stream()

    def thinking_start(self):
        self._thinking_buf = ""
        self._thinking_start_time = time.monotonic()
        if self.thinking_mode == "expanded":
            self.console.print(Text("💭 思考中...", style="dim"))

    def thinking_chunk(self, text: str):
        self._thinking_buf += text
        if self.thinking_mode == "expanded" and _IS_TTY:
            print(text, end="", flush=True)

    def thinking_end(self):
        elapsed = time.monotonic() - self._thinking_start_time
        n_chars = len(self._thinking_buf)
        if self.thinking_mode == "hidden":
            logger.debug(f"[思考] {n_chars} 字, {elapsed:.1f}s")
            self._had_thinking = True
            if self._thinking_buf:
                self._last_thinking = self._thinking_buf
            self._thinking_buf = ""
            return
        if self.thinking_mode == "expanded" and _IS_TTY:
            print()
            self.console.print(
                Text(f"💭 思考完毕 ({elapsed:.1f}s)", style="dim")
            )
        else:
            self.console.print(
                Text(f"💭 已思考 {n_chars} 字 ({elapsed:.1f}s)", style="dim")
            )
        self._had_thinking = True
        if self._thinking_buf:
            self._last_thinking = self._thinking_buf
            logger.debug(f"[思考内容] {self._thinking_buf[:500]}")
        self._thinking_buf = ""

    def tool_call_start(self, name: str, args_summary: str):
        self._tool_start_time = time.monotonic()
        if self.tool_detail == "minimal":
            self.console.print(Text(f"  🔧 {name}", style="dim"))
        elif self.tool_detail == "full":
            display_args = args_summary
            self.console.print(Text(f"  🔧 {name}({display_args})", style="dim"))
        else:
            display_args = args_summary[:80]
            self.console.print(Text(f"  🔧 {name}({display_args})", style="dim"))

    def tool_result(self, name: str, result: str, elapsed: float | None = None):
        if elapsed is None:
            elapsed = time.monotonic() - self._tool_start_time
        if self.tool_detail == "minimal":
            self.console.print(Text(f"    ✓ {elapsed:.1f}s", style="green"))
        elif self.tool_detail == "full":
            self.console.print(Text(f"    ✓ → {result} ({elapsed:.1f}s)", style="green"))
        else:
            preview = result.replace("\n", " ")[:200]
            suffix = "..." if len(result) > 200 else ""
            self.console.print(Text(f"    ✓ → {preview}{suffix} ({elapsed:.1f}s)", style="green"))

    def user_label(self, timestamp: str = ""):
        ts = f" [{timestamp[2:].replace('T', ' ')}]" if timestamp else ""
        self.console.print(Text(f"You{ts}", style="bold cyan"))

    def assistant_prefix(self, timestamp: str = ""):
        ts = f" [{timestamp[2:].replace('T', ' ')}]" if timestamp else ""
        self.console.print()
        self.console.print(Text(f"Assistant{ts}", style="bold"))

    def info(self, text: str):
        self.console.print(Text(text, style="dim"))

    def show_thinking(self):
        if not self._last_thinking:
            self.info("暂无思考记录")
            return
        from rich.panel import Panel
        from rich.markdown import Markdown
        self.console.print(Panel(Markdown(self._last_thinking), title="💭 思考过程", border_style="dim"))

    def set_thinking_mode(self, mode: str):
        if mode not in ("collapsed", "expanded", "hidden"):
            self.error(f"未知模式: {mode}，可选: collapsed / expanded / hidden")
            return
        self.thinking_mode = mode
        self.info(f"思考展示模式 → {mode}")

    def error(self, text: str):
        self.console.print(Text(f"✗ {text}", style="bold red"))

    def status_bar(self, model: str, context_length: int, prompt_tokens: int,
                    completion_tokens: int, system_prompt_chars: int,
                    history_count: int = 0):
        if not _IS_TTY:
            return
        usage_pct = (prompt_tokens / context_length * 100) if context_length else 0
        ctx_style = "cyan" if usage_pct < 70 else "yellow" if usage_pct < 85 else "bold red"
        info = Text()
        info.append("⚙ ", style="bold")
        info.append(model, style="bold blue")
        info.append(" │ ")
        info.append(f"{usage_pct:.0f}%", style=ctx_style)
        info.append(f" ({prompt_tokens}/{context_length})", style="dim")
        info.append(" │ ")
        info.append(f"↑{prompt_tokens} ↓{completion_tokens}", style="dim")
        info.append(" │ ")
        info.append(f"sys {system_prompt_chars}", style="dim")
        info.append(" │ ")
        info.append(f"msg {history_count}", style="dim")
        self.console.print(info, justify="right")

    def _reset_stream(self):
        self._stream_buf = ""
        self._streaming = False
        self._had_thinking = False
