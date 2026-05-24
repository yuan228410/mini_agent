"""压缩器：将旧对话归档为情景记忆 + 更新长期记忆/用户画像"""
import re
from datetime import datetime, timezone, timedelta

from logger import logger
from memory import COMPACT_PROMPT, MemoryStore

_UTC8 = timezone(timedelta(hours=8))


def _extract(tag: str, text: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


class Compactor:
    """智能压缩器。

    触发时机：prompt_tokens > context_length * context_usage_threshold
    压缩后保留：最近 keep_recent 条消息，且总字符不超过 char_threshold
    旧消息归档为三类产出：
      - episode: 写入今天的情景记忆文件
      - updated_memory: 更新 MEMORY.md（长期记忆）
      - updated_user: 更新 USER.md（用户画像）
    """

    def __init__(self, memory_store: MemoryStore, *,
                 keep_recent: int = 100, char_threshold: int = 50000,
                 context_usage_threshold: float = 0.8, context_length: int = 128000,
                 context_builder=None, skill_loader=None):
        self.memory = memory_store
        self.keep_recent = keep_recent
        self.char_threshold = char_threshold
        self.context_usage_threshold = context_usage_threshold
        self.context_length = context_length
        self.context_builder = context_builder
        self.skill_loader = skill_loader

    def should_compact(self, prompt_tokens: int) -> bool:
        if prompt_tokens <= 0:
            return False
        threshold = self.context_length * self.context_usage_threshold
        if prompt_tokens > threshold:
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {int(threshold)} ({self.context_usage_threshold*100:.0f}% of {self.context_length})")
            return True
        return False

    def compact(self, chat_fn, messages: list[dict]) -> list[dict]:
        non_system = [m for m in messages if m["role"] != "system"]
        recent = non_system[-self.keep_recent:]

        total_chars = sum(len(m.get("content") or "") for m in recent)
        while len(recent) > 1 and total_chars > self.char_threshold:
            removed = recent.pop(0)
            total_chars -= len(removed.get("content") or "")

        old = non_system[:len(non_system) - len(recent)]

        prompt = COMPACT_PROMPT.format(
            old_conversation=self._messages_to_text(old),
            current_memory=self.memory.read_memory() or "(空)",
            current_user=self.memory.read_user() or "(空)",
            today_episode=self.memory.read_today() or "(空)",
            now_hhmm=datetime.now(_UTC8).strftime("%H:%M"),
        )

        result = chat_fn([{"role": "user", "content": prompt}], tools=None)
        if not result:
            logger.warning("[压缩✗] 模型未返回结果")
            self.memory.mark_compacted()
            return [messages[0]] + recent

        text = result.get("content", "")
        episode = _extract("episode", text)
        new_memory = _extract("updated_memory", text)
        new_user = _extract("updated_user", text)

        if episode:
            self.memory.append_today(episode)
        if new_memory and new_memory != "(无需更新)":
            self.memory.write_memory(new_memory)
        if new_user and new_user != "(无需更新)":
            self.memory.write_user(new_user)

        self.memory.mark_compacted()

        if self.context_builder:
            messages[0]["content"] = self.context_builder.build(
                memory_store=self.memory,
                skill_loader=self.skill_loader,
            )
        else:
            parts = [messages[0]["content"]]
            if self.memory.has_memory():
                parts.append(f"## 长期记忆\n\n{self.memory.read_memory()}")
            if self.memory.has_user():
                parts.append(f"## 用户画像\n\n{self.memory.read_user()}")
            messages[0]["content"] = "\n\n---\n\n".join(parts)

        logger.debug(f"[压缩←] {len(old)} 条消息归档，保留 {len(recent)} 条 ({total_chars} 字符)")
        return [messages[0]] + recent

    @staticmethod
    def _messages_to_text(messages: list[dict]) -> str:
        parts = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")

            if isinstance(content, str):
                parts.append(f"[{role}] {content}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            parts.append(f"[{role}:tool] {block.get('name', '')}")
                        elif block.get("type") == "tool_result":
                            parts.append(f"[{role}:result] {str(block.get('content', ''))}")
        return "\n".join(parts)
