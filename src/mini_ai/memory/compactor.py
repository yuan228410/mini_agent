"""压缩器：按轮次摘要 + 三层记忆更新"""
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta

from ..logger import logger
from .store import MemoryStore
from ..llm.base import estimate_messages_tokens

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

BATCH_SUMMARY_PROMPT = """对以下各轮 Agent 执行过程分别进行简洁总结（每轮150字内）。

每轮请用 <round_N> 标记包裹：
<round_1>
- **需求**: 用户的要求
- **操作**: 调用的工具
- **结论**: 关键结果
</round_1>

<round_{count}>
...
</round_{count}>

各轮执行过程：
{all_rounds_text}"""


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
                 keep_recent: int = 50,
                 context_usage_threshold: float = 0.8, context_length: int = 128000,
                 keep_budget_ratio: float = 0.2,
                 early_compact_ratio: float = 0.85,
                max_cached_summaries: int = 200,
                max_summary_sections: int = 50,
                context_builder=None, skill_loader=None, history_db=None, project_path="",
                 summary_dir: Path | None = None):
        self.memory = memory_store
        self.keep_recent = keep_recent
        self.context_usage_threshold = context_usage_threshold
        self.context_length = context_length
        self.keep_budget_ratio = keep_budget_ratio
        self.early_compact_ratio = early_compact_ratio
        self.max_cached_summaries = max_cached_summaries
        self.max_summary_sections = max_summary_sections
        self.context_builder = context_builder
        self.skill_loader = skill_loader
        self.history_db = history_db
        self.project_path = project_path
        self.summary_dir = summary_dir
        # 增量压缩追踪：记录已摘要的轮次数量
        self._last_round_count = 0
        # 已摘要轮次缓存 {round_idx: summary_text}
        self._cached_summaries: dict[int, str] = {}

    def _hard_threshold(self) -> int:
        return int(self.context_length * self.context_usage_threshold)

    def _soft_threshold(self) -> int:
        return int(self._hard_threshold() * self.early_compact_ratio)

    def should_compact(self, prompt_tokens: int) -> bool:
        if prompt_tokens <= 0:
            return False
        threshold = self._hard_threshold()
        if prompt_tokens > threshold:
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {threshold}")
            return True
        # 两级预警：68% 阈值时尝试预压缩（仅增量）
        soft = self._soft_threshold()
        if prompt_tokens > soft and self._has_incremental_rounds():
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {soft} 触发预压缩")
            return True
        return False

    def _has_incremental_rounds(self) -> bool:
        return self._last_round_count > 0

    def estimate_tokens(self, messages: list[dict]) -> int:
        return estimate_messages_tokens(messages)

    def should_compact_local(self, messages: list[dict]) -> bool:
        estimated = self.estimate_tokens(messages)
        threshold = self._hard_threshold()
        if estimated > threshold:
            return True
        soft = self._soft_threshold()
        if estimated > soft and self._has_incremental_rounds():
            return True
        return False

    def compact(self, chat_fn, messages: list[dict], ctx=None, inject_fn=None) -> list[dict]:
        non_system = [m for m in messages if m["role"] != "system"]

        rounds = self._split_rounds(non_system)

        if len(rounds) <= 1:
            logger.info("[压缩] 轮次不足，跳过")
            return messages

        keep_rounds = self._determine_keep_rounds(rounds)
        old_rounds = rounds[:len(rounds) - keep_rounds]
        recent_rounds = rounds[len(rounds) - keep_rounds:]

        # 判断是否为增量压缩：上次已摘要的轮次本次依然在 old 中
        incremental = self._last_round_count > 0

        new_messages = [messages[0]]
        round_summaries = []
        new_round_summaries = []  # 仅新增摘要，用于写入文件
        skipped_chitchat = 0
        batch_candidates = []  # 需要批量摘要的轮次

        for i, rnd in enumerate(old_rounds):
            if _is_chitchat_round(rnd):
                skipped_chitchat += 1
                continue
            new_messages.append(rnd["user_msg"])
            execution = rnd["execution"]
            if not execution:
                continue

            # 增量模式：命中缓存直接复用，不再重复写入文件
            if incremental and i in self._cached_summaries:
                summary = self._cached_summaries[i]
                new_messages.append({
                    "role": "user",
                    "content": f"[第{i+1}轮执行摘要]\n{summary}",
                })
                continue

            # 小轮次直接内联，不需要调 LLM
            exec_text = self._messages_to_text(execution)
            if len(exec_text) < 100:
                new_messages.append({
                    "role": "user",
                    "content": f"[第{i+1}轮执行摘要]\n{exec_text}",
                })
                summary_entry = f"第{i+1}轮: {exec_text[:200]}"
                round_summaries.append(summary_entry)
                new_round_summaries.append(summary_entry)
                self._cached_summaries[i] = exec_text
                continue

            batch_candidates.append((i, rnd, execution, exec_text))

        if batch_candidates:
            summaries = self._batch_summarize(chat_fn, batch_candidates, ctx)
            for round_idx, summary in summaries:
                if summary:
                    new_messages.append({
                        "role": "user",
                        "content": f"[第{round_idx+1}轮执行摘要]\n{summary}",
                    })
                    summary_entry = f"第{round_idx+1}轮: {summary[:200]}"
                    round_summaries.append(summary_entry)
                    new_round_summaries.append(summary_entry)
                    self._cached_summaries[round_idx] = summary

        if skipped_chitchat > 0:
            logger.info(f"[压缩] 跳过 {skipped_chitchat} 轮闲聊")

        for rnd in recent_rounds:
            new_messages.append(rnd["user_msg"])
            new_messages.extend(rnd["execution"])

        self._update_memory(chat_fn, round_summaries, ctx)
        if new_round_summaries and self.summary_dir:
            self._write_summary(new_round_summaries)

        # 记忆更新后再提交追踪状态，避免 _update_memory 异常导致缓存不一致
        self._last_round_count = len(old_rounds)
        self._cached_summaries = {
            k: v for k, v in self._cached_summaries.items()
            if k < self._last_round_count
        }
        if len(self._cached_summaries) > self.max_cached_summaries:
            sorted_keys = sorted(self._cached_summaries.keys())
            for k in sorted_keys[:-self.max_cached_summaries]:
                del self._cached_summaries[k]

        if self.context_builder:
            new_messages[0]["content"] = self.context_builder.build(
                memory_store=self.memory,
                skill_loader=self.skill_loader,
                project_path=self.project_path,
            )

        # 重新注入 todos（修复压缩后丢失任务计划的问题）
        if inject_fn:
            inject_fn(new_messages)

        logger.info(f"[压缩←] {len(old_rounds)} 轮摘要{' (增量)' if incremental else ''}，保留 {len(recent_rounds)} 轮完整")
        return new_messages

    def _write_summary(self, round_summaries: list[str]):
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        path = self.summary_dir / "compaction_summary.md"
        ts = datetime.now(_UTC8).strftime("%Y-%m-%d %H:%M")
        lines = [f"\n## 压缩 {ts}\n"]
        for s in round_summaries:
            lines.append(f"- {s}")
        lines.append("")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
        if path.exists():
            text = path.read_text(encoding="utf-8")
            sections = text.split("\n## 压缩 ")
            if len(sections) > self.max_summary_sections:
                path.write_text("## 压缩 " + "\n## 压缩 ".join(sections[-self.max_summary_sections:]), encoding="utf-8")

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
        """用 token 预算决定保留多少轮。"""
        threshold = self.context_length * self.context_usage_threshold
        # 保留预算 = 硬阈值以下留一定比例余量
        keep_budget = int(threshold * self.keep_budget_ratio)
        if keep_budget < 2000:
            keep_budget = 2000

        keep = 0
        total_tokens = 0
        for rnd in reversed(rounds):
            rnd_msgs = [rnd["user_msg"]] + rnd["execution"]
            rnd_tokens = estimate_messages_tokens(rnd_msgs)
            if total_tokens + rnd_tokens > keep_budget and keep >= 2:
                break
            total_tokens += rnd_tokens
            keep += 1
            if keep >= len(rounds) - 1:
                break
        return max(keep, 2)

    def _batch_summarize(self, chat_fn, candidates: list[tuple], ctx=None) -> list[tuple[int, str]]:
        """批量摘要多轮执行过程，一次 LLM 调用完成所有轮的摘要。
        自动分批处理，每批不超过 12000 字符，避免截断丢失后续轮次。
        """
        if not candidates:
            return []

        all_summaries: dict[int, str] = {}

        # 分批，每批 prompt 总长不超过 12000
        MAX_BATCH_CHARS = 12000
        batches = []
        current_batch = []
        current_chars = len(BATCH_SUMMARY_PROMPT.format(count=1, all_rounds_text=""))

        for i, rnd, execution, exec_text in candidates:
            entry_text = f"--- 第{i+1}轮 ---\n{exec_text[:4000]}"
            entry_chars = len(entry_text) + 20  # 20 for `<round_N>` wrapper overhead

            if current_chars + entry_chars > MAX_BATCH_CHARS and current_batch:
                batches.append((current_batch, current_chars))
                current_batch = []
                current_chars = len(BATCH_SUMMARY_PROMPT.format(count=1, all_rounds_text=""))

            current_batch.append((i, rnd, execution, entry_text))
            current_chars += entry_chars

        if current_batch:
            batches.append((current_batch, current_chars))

        for batch in batches:
            batch_candidates = batch[0]
            all_text = "\n\n".join(entry_text for _, _, _, entry_text in batch_candidates)
            count = len(batch_candidates)

            prompt = BATCH_SUMMARY_PROMPT.format(count=count, all_rounds_text=all_text)

            result = chat_fn([{"role": "user", "content": prompt}], tools=None, ctx=ctx)

            if result and result.get("content"):
                for i, rnd, execution, entry_text in batch_candidates:
                    tag = f"round_{i+1}"
                    summary = _extract(tag, result["content"])
                    if summary:
                        all_summaries[i] = summary
                    else:
                        all_summaries[i] = self._messages_to_text(execution)[:500]
                logger.debug(f"[压缩] 批量摘要 {len(batch_candidates)} 轮成功")
            else:
                for i, rnd, execution, entry_text in batch_candidates:
                    all_summaries[i] = self._messages_to_text(execution)[:500]

        return sorted(all_summaries.items())

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
        logger.info("[压缩] 记忆更新完成")

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
