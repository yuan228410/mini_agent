"""终端 UI 渲染层：Markdown 渲染、思维链展示、工具调用展示"""
import sys
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text

from .logger import logger

_IS_TTY = sys.stdout.isatty()

_SLASH_COMMANDS = [
    ("/save", "保存当前对话为命名会话"),
    ("/load", "加载已保存的会话"),
    ("/sessions", "列出所有已保存的会话"),
    ("/compact", "手动触发对话压缩"),
    ("/clear", "清空历史消息"),
    ("/genskill", "从对话中总结生成技能"),
    ("/skill", "使用指定技能执行任务"),
    ("/thinking", "查看最近思考过程"),
    ("/thinking collapsed", "折叠模式（仅摘要）"),
    ("/thinking expanded", "展开模式（实时显示）"),
    ("/thinking hidden", "隐藏思考过程"),
    ("/exit", "退出"),
]

def _build_completions():
    items = list(_SLASH_COMMANDS)
    try:
        from .skills import SkillLoader
        from .config import DATA_DIR, SKILL_PATHS
        loader = SkillLoader(DATA_DIR / "skills", SKILL_PATHS)
        for name, skill in loader.skills.items():
            desc = skill["meta"].get("description", "")
            items.append((f"/skill {name}", desc))
    except Exception:
        pass
    return items

_ALL_COMPLETIONS = _build_completions()


class Display:
    def __init__(self, thinking_mode: str = "collapsed", tool_detail: str = "summary"):
        self.console = Console()
        self.thinking_mode = thinking_mode
        self.tool_detail = tool_detail
        self._stream_buf = ""
        self._streaming = False
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
        from . import __version__
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

    def user_input(self) -> str:
        if _IS_TTY and sys.stdin.isatty():
            return self._prompt_input()
        try:
            line = input("mini-ai> ")
        except EOFError:
            return "exit"
        if not line and not sys.stdin.isatty():
            return "exit"
        return line

    def _prompt_input(self) -> str:
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
        try:
            return self._session.prompt()
        except (EOFError, KeyboardInterrupt):
            return "exit"

    def text_chunk(self, text: str):
        self._stream_buf += text
        if _IS_TTY:
            if not self._streaming:
                sys.stdout.write("\033[s")
                sys.stdout.flush()
                self._streaming = True
            print(text, end="", flush=True)

    def text_end(self, full_text: str | None = None):
        content = full_text if full_text is not None else self._stream_buf
        if not content:
            self._reset_stream()
            return

        if self._had_thinking:
            self.console.print()
            self.console.print(Text("Assistant", style="bold"))
            self._had_thinking = False

        if _IS_TTY and self._streaming:
            sys.stdout.write("\033[u")
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

    def assistant_prefix(self):
        self.console.print()
        self.console.print(Text("Assistant", style="bold"))

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

    def _reset_stream(self):
        self._stream_buf = ""
        self._streaming = False
        self._had_thinking = False
