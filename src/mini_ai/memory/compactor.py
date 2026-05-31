"""压缩器：按轮次摘要 + 缓存管理。

压缩策略：
1. 保留所有 user 消息（用户意图不能丢失）
2. 每轮 user→(assistant+tool 执行过程)→下一个 user 之间的消息独立摘要
3. 压缩后结构：system → user1 → summary1 → user2 → summary2 → ...
4. 最近 keep_recent_rounds 轮不压缩（保持完整上下文）
5. 摘要完成后委托 MemoryUpdater 更新三层记忆
6. 摘要文件 I/O 委托给 SummaryWriter
"""
from pathlib import Path
from datetime import datetime, timezone, timedelta

from ..logger import logger
from .store import MemoryStore
from ._utils import extract_tag as _extract
from .updater import MemoryUpdater
from ..llm.base import estimate_messages_tokens

_UTC8 = timezone(timedelta(hours=8))

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


# ═══════════════════════════════════════════
# SummaryWriter — 摘要文件 I/O
# ═══════════════════════════════════════════

class SummaryWriter:
    """将压缩摘要持久化到 markdown 文件，支持滚动保留最近 N 个 section。"""

    def __init__(self, summary_dir: Path, max_sections: int = 50):
        self.summary_dir = summary_dir
        self.max_sections = max_sections

    def write(self, round_summaries: list[str]) -> None:
        """追加一批摘要到 compaction_summary.md。"""
        if not round_summaries:
            return
        self.summary_dir.mkdir(parents=True, exist_ok=True)
        path = self.summary_dir / "compaction_summary.md"
        logger.debug(f"[压缩摘要] 追加 {len(round_summaries)} 条摘要到 {path}")
        ts = datetime.now(_UTC8).strftime("%Y-%m-%d %H:%M")
        lines = [f"\n## 压缩 {ts}\n"]
        for s in round_summaries:
            lines.append(f"- {s}")
        lines.append("")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self._trim(path)

    def _trim(self, path: Path) -> None:
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        sections = text.split("\n## 压缩 ")
        if len(sections) > self.max_sections:
            logger.info(f"[压缩摘要] 裁剪: {len(sections)} -> {self.max_sections} 条")
            path.write_text(
                "## 压缩 " + "\n## 压缩 ".join(sections[-self.max_sections:]),
                encoding="utf-8",
            )


# ═══════════════════════════════════════════
# Compactor
# ═══════════════════════════════════════════

class Compactor:
    """智能压缩器 — 按轮次摘要，保留用户意图。

    只负责压缩逻辑：拆分轮次 → 摘要 → 重组消息。
    记忆更新委托给 MemoryUpdater，文件 I/O 委托给 SummaryWriter。
    """

    def __init__(self, memory_store: MemoryStore, *,
                 keep_recent: int = 50,
                 context_usage_threshold: float = 0.8,
                 context_length: int = 256000,
                 keep_budget_ratio: float = 0.2,
                 early_compact_ratio: float = 0.85,
                 max_cached_summaries: int = 200,
                 max_summary_sections: int = 50,
                 context_builder=None,
                 skill_loader=None,
                 project_path="",
                 summary_dir: Path | None = None):
        self.memory = memory_store
        self.keep_recent = keep_recent
        self.context_usage_threshold = context_usage_threshold
        self.context_length = context_length
        self.keep_budget_ratio = keep_budget_ratio
        self.early_compact_ratio = early_compact_ratio
        self.max_cached_summaries = max_cached_summaries
        self.context_builder = context_builder
        self.skill_loader = skill_loader
        self.project_path = project_path

        # 委托
        self._memory_updater = MemoryUpdater(memory_store)
        self._summary_writer = SummaryWriter(summary_dir, max_summary_sections) if summary_dir else None

        # 增量压缩追踪
        self._last_round_count = 0
        self._cached_summaries: dict[int, str] = {}

    # ── 阈值判断 ──

    def _hard_threshold(self) -> int:
        return int(self.context_length * self.context_usage_threshold)

    def _soft_threshold(self) -> int:
        return int(self._hard_threshold() * self.early_compact_ratio)

    def _has_incremental_rounds(self) -> bool:
        return self._last_round_count > 0

    def should_compact(self, prompt_tokens: int) -> bool:
        if prompt_tokens <= 0:
            return False
        threshold = self._hard_threshold()
        if prompt_tokens > threshold:
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {threshold}")
            return True
        soft = self._soft_threshold()
        if prompt_tokens > soft and self._has_incremental_rounds():
            logger.info(f"[压缩→] prompt_tokens={prompt_tokens} > {soft} 触发预压缩")
            return True
        return False

    def estimate_tokens(self, messages: list[dict]) -> int:
        return estimate_messages_tokens(messages)

    def should_compact_local(self, messages: list[dict]) -> bool:
        estimated = self.estimate_tokens(messages)
        threshold = self._hard_threshold()
        if estimated > threshold:
            logger.info(f"[压缩→] 本地估算 tokens={estimated} > hard阈值={threshold}")
            return True
        soft = self._soft_threshold()
        if estimated > soft and self._has_incremental_rounds():
            logger.info(f"[压缩→] 本地估算 tokens={estimated} > soft阈值={soft}，触发预压缩")
            return True
        return False

    # ── 主压缩流程 ──

    def compact(self, chat_fn, messages: list[dict], ctx=None, inject_fn=None) -> list[dict]:
        non_system = [m for m in messages if m["role"] != "system"]

        rounds = self._split_rounds(non_system)

        if len(rounds) <= 1:
            logger.info("[压缩] 轮次不足，跳过")
            return messages

        logger.info(f"[压缩→] 开始: {len(messages)} 条消息, {len(rounds)} 轮, 阈值 hard={self._hard_threshold()} soft={self._soft_threshold()}")

        keep_rounds = self._determine_keep_rounds(rounds)
        old_rounds = rounds[:len(rounds) - keep_rounds]
        recent_rounds = rounds[len(rounds) - keep_rounds:]

        incremental = self._last_round_count > 0
        logger.info(f"[压缩→] 拆分: 摘要 {len(old_rounds)} 轮, 保留 {keep_rounds} 轮完整, 增量={'是' if incremental else '否'}")

        new_messages = [messages[0]]
        round_summaries = []
        new_round_summaries = []
        skipped_chitchat = 0
        batch_candidates = []

        for i, rnd in enumerate(old_rounds):
            if _is_chitchat_round(rnd):
                skipped_chitchat += 1
                logger.debug(f"[压缩] 第{i+1}轮为闲聊，跳过")
                continue
            new_messages.append(rnd["user_msg"])
            execution = rnd["execution"]
            if not execution:
                continue

            if incremental and i in self._cached_summaries:
                summary = self._cached_summaries[i]
                logger.debug(f"[压缩] 第{i+1}轮命中缓存摘要")
                new_messages.append({
                    "role": "user",
                    "content": f"[第{i+1}轮执行摘要]\n{summary}",
                })
                continue

            exec_text = self._messages_to_text(execution)
            if len(exec_text) < 100:
                logger.debug(f"[压缩] 第{i+1}轮执行过程过短({len(exec_text)}字)，直接保留")
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
            logger.info(f"[压缩→] 批量摘要: {len(batch_candidates)} 轮待摘要")
            summaries = self._batch_summarize(chat_fn, batch_candidates, ctx)
            logger.info(f"[压缩→] 批量摘要完成: {len(summaries)} 条结果")
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

        # 委托记忆更新
        logger.info(f"[压缩→] 更新记忆: {len(round_summaries)} 条摘要")
        self._memory_updater.update(chat_fn, round_summaries, ctx)
        # 委托摘要 I/O
        if self._summary_writer:
            self._summary_writer.write(new_round_summaries)

        # 追踪状态
        self._last_round_count = len(old_rounds)
        self._cached_summaries = {
            k: v for k, v in self._cached_summaries.items()
            if k < self._last_round_count
        }
        if len(self._cached_summaries) > self.max_cached_summaries:
            sorted_keys = sorted(self._cached_summaries.keys())
            evict = sorted_keys[:-self.max_cached_summaries]
            for k in evict:
                del self._cached_summaries[k]
            logger.debug(f"[压缩] 缓存淘汰: 移除 {len(evict)} 条旧摘要, 剩余 {len(self._cached_summaries)}")

        if self.context_builder:
            logger.info("[压缩→] 重建 system prompt")
            new_messages[0]["content"] = self.context_builder.build(
                memory_store=self.memory,
                skill_loader=self.skill_loader,
                project_path=self.project_path,
            )

        if inject_fn:
            inject_fn(new_messages)

        before_tokens = estimate_messages_tokens(messages)
        after_tokens = estimate_messages_tokens(new_messages)
        logger.info(f"[压缩←] 完成: {len(messages)} -> {len(new_messages)} 条消息, ~{before_tokens} -> ~{after_tokens} tokens, {len(old_rounds)} 轮摘要{' (增量)' if incremental else ''}, 保留 {len(recent_rounds)} 轮完整")
        return new_messages

    # ── 轮次拆分 ──

    def _split_rounds(self, non_system: list[dict]) -> list[dict]:
        logger.debug(f"[压缩] 拆分轮次: {len(non_system)} 条非system消息")
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

        logger.debug(f"[压缩] 拆分完成: {len(rounds)} 轮")
        return rounds

    def _determine_keep_rounds(self, rounds: list[dict]) -> int:
        threshold = self.context_length * self.context_usage_threshold
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
        logger.debug(f"[压缩] keep_budget={keep_budget}, 保留 {max(keep, 2)}/{len(rounds)} 轮, total_tokens={total_tokens}")
        return max(keep, 2)

    # ── 批量摘要 ──

    def _batch_summarize(self, chat_fn, candidates: list[tuple], ctx=None) -> list[tuple[int, str]]:
        if not candidates:
            return []

        all_summaries: dict[int, str] = {}

        MAX_BATCH_CHARS = 12000
        batches = []
        current_batch = []
        current_chars = len(BATCH_SUMMARY_PROMPT.format(count=1, all_rounds_text=""))

        for i, rnd, execution, exec_text in candidates:
            entry_text = f"--- 第{i+1}轮 ---\n{exec_text[:4000]}"
            entry_chars = len(entry_text) + 20

            if current_chars + entry_chars > MAX_BATCH_CHARS and current_batch:
                batches.append((current_batch, current_chars))
                current_batch = []
                current_chars = len(BATCH_SUMMARY_PROMPT.format(count=1, all_rounds_text=""))

            current_batch.append((i, rnd, execution, entry_text))
            current_chars += entry_chars

        if current_batch:
           batches.append((current_batch, current_chars))

        logger.info(f"[压缩→] 批量摘要: {len(candidates)} 轮, 分 {len(batches)} 批")

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
                        logger.debug(f"[压缩] 第{i+1}轮 LLM未提取到<round_{i+1}>标签，降级为原文")
                        all_summaries[i] = self._messages_to_text(execution)[:500]
                logger.debug(f"[压缩] 批量摘要 {len(batch_candidates)} 轮成功, 轮次: {[i+1 for i,_,_,_ in batch_candidates]}")
            else:
                logger.warning(f"[压缩] 批量摘要 LLM 返回为空, {len(batch_candidates)} 轮降级为原文截断")
                for i, rnd, execution, entry_text in batch_candidates:
                    all_summaries[i] = self._messages_to_text(execution)[:500]

        return sorted(all_summaries.items())

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
