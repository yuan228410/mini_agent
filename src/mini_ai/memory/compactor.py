"""压缩器：按轮次摘要 + 三层记忆更新"""
import re
from datetime import datetime, timezone, timedelta

from ..logger import logger
from .store import MemoryStore

_UTC8 = timezone(timedelta(hours=8))

COMPACT_PROMPT = """根据对话轮次摘要，更新长期记忆和用户画像：

<episode>
今日关键记录（事实、结论、待办），用于每日回顾。保持简洁。
</episode>

<updated_memory>
如果产生了值得长期记住的信息（目标、决策、偏好、项目背景），更新长期记忆。
先写保留的旧记忆，再写新增内容。无需更新则写"(无需更新)"。
</updated_memory>

<updated_user>
如果更了解了用户的偏好、习惯、背景，更新用户画像。无需更新则写"(无需更新)"。
</updated_user>

当前长期记忆：
{current_memory}

当前用户画像：
{current_user}

今天的已有记录：
{today_episode}

各轮次摘要：
{round_summaries}"""

ROUND_SUMMARY_PROMPT = """简洁总结以下 Agent 执行过程（1000字内）：
- 完成了什么任务
- 调用了哪些工具
- 关键结果和发现

执行过程：
{execution_content}"""


_CHITCHAT_KEYWORDS = frozenset([
    "你好", "谢谢", "嗯", "哈哈", "ok", "好的", "再见", "hello", "hi", "thanks",
    "thank you", "bye", "嗯嗯", "哦", "啊", "呢", "吧", "了解", "明白",
    "good", "nice", "cool", "great", "👍", "🙏",
])


def _is_chitchat_round(rnd: dict) -> bool:
    user_content = (rnd["user_msg"].get("content") or "").strip()
    execution = rnd["execution"]

    if len(user_content) > 30:
        return False

    has_tool_calls = any(msg.get("tool_calls") for msg in execution)
    if has_tool_calls:
        return False

    assistant_content = ""
    for msg in execution:
        if msg.get("role") == "assistant":
            assistant_content += msg.get("content") or ""
    if len(assistant_content) > 100:
        return False

    if any(kw in user_content.lower() for kw in _CHITCHAT_KEYWORDS):
        return True

    if len(user_content) < 10 and not execution:
        return True

    return False


def _extract(tag: str, text: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else None


class Compactor:
    """智能压缩器 — 按轮次摘要，保留用户意图。

    压缩策略：
    1. 保留所有 user 消息（用户意图不能丢失）
    2. 每轮 user→(assistant+tool 执行过程)→下一个 user 之间的消息独立摘要
    3. 压缩后结构：system → user1 → summary1 → user2 → summary2 → ...
    4. 最近 keep_recent_rounds 轮不压缩（保持完整上下文）
    5. 摘要完成后更新三层记忆
    """

    def __init__(self, memory_store: MemoryStore, *,
                 keep_recent: int = 50, char_threshold: int = 20000,
                 context_usage_threshold: float = 0.8, context_length: int = 128000,
                 context_builder=None, skill_loader=None, history_db=None, project_path=""):
        self.memory = memory_store
        self.keep_recent = keep_recent
        self.char_threshold = char_threshold
        self.context_usage_threshold = context_usage_threshold
        self.context_length = context_length
        self.context_builder = context_builder
        self.skill_loader = skill_loader
        self.history_db = history_db
        self.project_path = project_path

    def should_compact(self, prompt_tokens: int) -> bool:
        if prompt_tokens <= 0:
            return False
        threshold = self.context_length * self.context_usage_threshold
        if prompt_tokens > threshold:
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {int(threshold)}")
            return True
        return False

    def estimate_tokens(self, messages: list[dict]) -> int:
        total_chars = 0
        for msg in messages:
            content = msg.get("content") or ""
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                total_chars += len(str(content))
        return int(total_chars / 2.5)

    def should_compact_local(self, messages: list[dict]) -> bool:
        estimated = self.estimate_tokens(messages)
        threshold = self.context_length * self.context_usage_threshold
        return estimated > threshold

    def compact(self, chat_fn, messages: list[dict], ctx=None) -> list[dict]:
        non_system = [m for m in messages if m["role"] != "system"]

        rounds = self._split_rounds(non_system)

        if len(rounds) <= 1:
            logger.info("[压缩] 轮次不足，跳过")
            return messages

        keep_rounds = self._determine_keep_rounds(rounds)
        old_rounds = rounds[:len(rounds) - keep_rounds]
        recent_rounds = rounds[len(rounds) - keep_rounds:]

        new_messages = [messages[0]]
        round_summaries = []
        skipped_chitchat = 0

        for i, rnd in enumerate(old_rounds):
            if _is_chitchat_round(rnd):
                skipped_chitchat += 1
                continue
            new_messages.append(rnd["user_msg"])
            execution = rnd["execution"]
            if execution:
                summary = self._summarize_round(chat_fn, execution, i + 1, ctx)
                if summary:
                    new_messages.append({
                        "role": "user",
                        "content": f"[第{i+1}轮执行摘要]\n{summary}",
                    })
                    round_summaries.append(f"第{i+1}轮: {summary[:200]}")

        if skipped_chitchat > 0:
            logger.info(f"[压缩] 跳过 {skipped_chitchat} 轮闲聊")

        for rnd in recent_rounds:
            new_messages.append(rnd["user_msg"])
            new_messages.extend(rnd["execution"])

        self._update_memory(chat_fn, round_summaries, ctx)
        if self.history_db:
            self.history_db.mark_archived()

        if self.context_builder:
            new_messages[0]["content"] = self.context_builder.build(
                memory_store=self.memory,
                skill_loader=self.skill_loader,
                project_path=self.project_path,
            )

        logger.info(f"[压缩←] {len(old_rounds)} 轮摘要，保留 {len(recent_rounds)} 轮完整")
        return new_messages

    def _split_rounds(self, non_system: list[dict]) -> list[dict]:
        rounds = []
        current_user = None
        current_execution = []

        for msg in non_system:
            if msg["role"] == "user" and not (msg.get("content") or "").startswith("[第"):
                if current_user is not None:
                    rounds.append({"user_msg": current_user, "execution": current_execution})
                current_user = msg
                current_execution = []
            else:
                current_execution.append(msg)

        if current_user is not None:
            rounds.append({"user_msg": current_user, "execution": current_execution})

        return rounds

    def _determine_keep_rounds(self, rounds: list[dict]) -> int:
        keep = 0
        total_chars = 0
        for rnd in reversed(rounds):
            rnd_chars = len(rnd["user_msg"].get("content") or "")
            for msg in rnd["execution"]:
                rnd_chars += len(msg.get("content") or "")
            if total_chars + rnd_chars > self.char_threshold and keep >= 2:
                break
            total_chars += rnd_chars
            keep += 1
            if keep >= len(rounds) - 1:
                break
        return max(keep, 2)

    def _summarize_round(self, chat_fn, execution: list[dict], round_num: int, ctx=None) -> str:
        content = self._messages_to_text(execution)
        if not content.strip():
            return ""

        if len(content) < 100:
            return content

        prompt = ROUND_SUMMARY_PROMPT.format(execution_content=content[:8000])
        result = chat_fn([{"role": "user", "content": prompt}], tools=None, ctx=ctx)
        if result and result.get("content"):
            logger.debug(f"[压缩] 第{round_num}轮摘要生成成功")
            return result["content"]
        return content[:500]

    def _update_memory(self, chat_fn, round_summaries: list[str], ctx=None):
        if not round_summaries:
            return

        prompt = COMPACT_PROMPT.format(
            current_memory=self.memory.read_memory() or "(空)",
            current_user=self.memory.read_user() or "(空)",
            today_episode=self.memory.read_today() or "(空)",
            round_summaries="\n\n".join(round_summaries),
        )

        result = chat_fn([{"role": "user", "content": prompt}], tools=None, ctx=ctx)
        if not result:
            return

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

    @staticmethod
    def _messages_to_text(messages: list[dict]) -> str:
        parts = []
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")
            if isinstance(content, str):
                if msg.get("tool_calls"):
                    names = [tc["function"]["name"] for tc in msg["tool_calls"]]
                    parts.append(f"[{role}] 调用工具: {', '.join(names)}")
                elif content:
                    parts.append(f"[{role}] {content[:500]}")
            elif isinstance(content, list):
                parts.append(f"[{role}] (结构化内容)")
        return "\n".join(parts)
