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

    触发时机：消息数 > K 且总字符数 > 阈值
    保留最近 K 条消息，调用模型将旧消息归档为三类产出：
      - episode: 写入今天的情景记忆文件
      - updated_memory: 更新 MEMORY.md（长期记忆）
      - updated_user: 更新 USER.md（用户画像）
    """

    def __init__(self, memory_store: MemoryStore, keep_recent: int = 10, char_threshold: int = 8000):
        self.memory = memory_store
        self.keep_recent = keep_recent
        self.char_threshold = char_threshold

    def should_compact(self, messages: list[dict]) -> bool:
        """判断是否需要压缩"""
        non_system = [m for m in messages if m["role"] != "system"]
        if len(non_system) <= self.keep_recent:
            return False
        total_chars = sum(len(m.get("content") or "") for m in non_system)
        return total_chars > self.char_threshold

    def compact(self, chat_fn, messages: list[dict]) -> list[dict]:
        """压缩旧消息，返回精简后的消息列表"""
        non_system = [m for m in messages if m["role"] != "system"]
        old = non_system[: -self.keep_recent]
        recent = non_system[-self.keep_recent :]

        prompt = COMPACT_PROMPT.format(
            old_conversation=self._messages_to_text(old),
            current_memory=self.memory.read_memory() or "(空)",
            current_user=self.memory.read_user() or "(空)",
            today_episode=self.memory.read_today() or "(空)",
            now_hhmm=datetime.now(_UTC8).strftime("%H:%M"),
        )

        result = chat_fn([{"role": "user", "content": prompt}], tools=None)
        if not result:
            logger.warning("[压缩失败] 模型未返回结果")
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

        # 将长期记忆/用户画像合并到主 system prompt 中
        parts = [messages[0]["content"]]
        if self.memory.has_memory():
            parts.append(f"## 长期记忆\n\n{self.memory.read_memory()}")
        if self.memory.has_user():
            parts.append(f"## 用户画像\n\n{self.memory.read_user()}")
        messages[0]["content"] = "\n\n---\n\n".join(parts)

        logger.info(f"[压缩完成] {len(old)} 条消息归档")
        return [messages[0]] + recent

    @staticmethod
    def _messages_to_text(messages: list[dict]) -> str:
        """将消息列表转为可读文本"""
        parts = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")

            if isinstance(content, str):
                parts.append(f"[{role}] {content[:500]}")
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "tool_use":
                            parts.append(f"[{role}:tool] {block.get('name', '')}")
                        elif block.get("type") == "tool_result":
                            parts.append(f"[{role}:result] {str(block.get('content', ''))[:200]}")
        return "\n".join(parts)