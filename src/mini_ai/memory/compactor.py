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
from datetime import datetime
from ..utils import _UTC8

from ..logger import logger
from .store import MemoryStore
from ._utils import extract_tag as _extract
from .updater import MemoryUpdater
from ..llm.base import estimate_messages_tokens
from .context_pruner import ContextPruner, PruneOptions

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

    def compact(self, chat_fn, messages: list[dict], ctx=None, inject_fn=None, keep_recent_override: int | None = None) -> list[dict]:
        non_system = [m for m in messages if m["role"] != "system"]

        rounds = self._split_rounds(non_system)

        if len(rounds) <= 1:
            logger.info("[压缩] 轮次不足，跳过")
            return messages

        logger.info(f"[压缩→] 开始: {len(messages)} 条消息, {len(rounds)} 轮, 阈值 hard={self._hard_threshold()} soft={self._soft_threshold()}")

        if keep_recent_override is not None:
            keep_rounds = min(keep_recent_override, len(rounds))
            logger.info(f"[压缩→] keep_recent_override={keep_recent_override}, keep_rounds={keep_rounds}")
        else:
            keep_rounds = self._determine_keep_rounds(rounds)
        old_rounds = rounds[:len(rounds) - keep_rounds]
        recent_rounds = rounds[len(rounds) - keep_rounds:]

        incremental = self._last_round_count > 0
        logger.info(f"[压缩→] 拆分: 摘要 {len(old_rounds)} 轮, 保留 {keep_rounds} 轮完整, 增量={'是' if incremental else '否'}")

        new_messages = [messages[0]]
        round_summaries = []
        new_round_summaries = []
        batch_candidates = []

        # 🔧 关键说明：
        # old_rounds = rounds[:len(rounds) - keep_rounds]，即 rounds 的前 N 个元素
        # 因此 old_rounds[i] 对应 rounds[i]，global_idx = i 是正确的
        # 无论首次压缩还是增量压缩，这个关系都成立
        old_round_start_idx = 0
        
        for i, rnd in enumerate(old_rounds):
            global_idx = old_round_start_idx + i  # 全局轮次索引（在 rounds 中的位置）
            
            # 🔧 修复：不再跳过闲聊轮次，所有轮次一视同仁
            new_messages.append(rnd["user_msg"])
            execution = rnd["execution"]
            if not execution:
                continue

            if incremental and global_idx in self._cached_summaries:
                summary = self._cached_summaries[global_idx]
                logger.debug(f"[压缩] 第{global_idx+1}轮命中缓存摘要")
                # 🔧 修复：用元数据标记摘要消息，而非伪装成普通 user 消息
                new_messages.append({
                    "role": "user",
                    "content": f"[第{global_idx+1}轮执行摘要]\n{summary}",
                    "_is_summary": True,  # 标记为摘要，避免被误判为用户输入
                })
                continue

            exec_text = self._messages_to_text(execution)
            if len(exec_text) < 100:
                logger.debug(f"[压缩] 第{global_idx+1}轮执行过程过短({len(exec_text)}字)，直接保留")
                new_messages.append({
                    "role": "user",
                    "content": f"[第{global_idx+1}轮执行摘要]\n{exec_text}",
                    "_is_summary": True,
                })
                summary_entry = f"第{global_idx+1}轮: {exec_text[:200]}"
                round_summaries.append(summary_entry)
                new_round_summaries.append(summary_entry)
                self._cached_summaries[global_idx] = exec_text
                continue

            batch_candidates.append((global_idx, rnd, execution, exec_text))

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
        self._last_round_count = len(rounds)  # 记录总轮次数
        
        # 🔧 缓存清理说明：
        # 只保留 old_rounds 范围内的摘要缓存（索引 0 到 len(old_rounds)-1）
        # recent_rounds 会被完整保留到消息列表，不需要摘要缓存
        # 清理条件：k < len(old_rounds) 确保只保留有效范围的缓存
        self._cached_summaries = {
            k: v for k, v in self._cached_summaries.items()
            if k < len(old_rounds)
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
            # 🔧 修复：用元数据 _is_summary 判断摘要消息，而非依赖内容
            is_user = msg["role"] == "user"
            is_summary = msg.get("_is_summary", False)
            
            if is_user and not is_summary:
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

    # ── 渐进式恢复 ──

    def force_compact(self, chat_fn, messages: list[dict], ctx=None, max_compact_calls: int = 3) -> bool:
        """上下文溢出后的渐进式恢复

        5 级逐步加码裁剪+压缩，每级先裁剪工具结果（零开销），
        再判断是否需要调 LLM 做摘要压缩。

        Returns:
            True = 恢复成功，messages 已替换，可重试 LLM
            False = 所有级别耗尽，仍超限
        """
        # P2#13: 清空摘要缓存，避免跨级脏数据
        self._cached_summaries.clear()
        self._last_round_count = 0

        safe_threshold = int(self.context_length * 0.7)

        # 5 级渐进参数（对标 my_agent）
        # (hard_prune_after, max_tool_result_chars, soft_prune_lines, keep_recent_override, name)
        # keep_recent_override: None=逐级减半, >0=强制值
        levels = [
            (10, 2000, 5, None,           "L0"),  # 默认裁剪 + 强制压缩
            (5,  1000, 4, None,           "L1"),  # 加码裁剪
            (3,   600, 3, None,           "L2"),  # 激进裁剪
            (0,   400, 3, 3,              "L3"),  # 全量裁剪 + keep_recent=3
            (0,   200, 2, 1,              "L4"),  # 最激进裁剪 + keep_recent=1
        ]

        compact_call_count = 0

        for i, (hard_after, max_chars, soft_lines, keep, name) in enumerate(levels):
            logger.info(f"[force_compact] {name} 开始, messages={len(messages)}, tokens={estimate_messages_tokens(messages)}")

            # 第一步：裁剪工具结果（纯本地，零开销）
            opts = PruneOptions(
                protect_recent=3,
                hard_prune_after=hard_after,
                max_tool_result_chars=max_chars,
                soft_prune_lines=soft_lines,
            )
            pruned = ContextPruner.prune(messages, opts)
            estimated = estimate_messages_tokens(pruned)

            logger.info(f"[force_compact] {name} 裁剪后估算 tokens={estimated}, 安全线={safe_threshold}")

            if estimated < safe_threshold:
                messages[:] = pruned
                logger.info(f"[force_compact] {name} 仅裁剪即足够")
                return True

            # 第二步：压缩（需调 LLM 生成摘要，成本高）
            if compact_call_count >= max_compact_calls:
                logger.warning(f"[force_compact] {name} 已达 max_compact_calls={max_compact_calls}，跳过压缩")
                messages[:] = pruned
                continue

            try:
                # L0-L2: keep=None，使用 keep_recent 逐级减半
                # L3-L4: keep=3/1，强制保留更少轮次
                keep_override = keep if keep is not None else max(self.keep_recent // (2 ** i), 2)
                new_msgs = self.compact(chat_fn, pruned, ctx=ctx, keep_recent_override=keep_override)
                compact_call_count += 1
                estimated = estimate_messages_tokens(new_msgs)

                logger.info(f"[force_compact] {name} 压缩后({compact_call_count}/{max_compact_calls}), msgs={len(new_msgs)}, tokens={estimated}")

                if estimated < safe_threshold:
                    messages[:] = new_msgs
                    logger.info(f"[force_compact] {name} 恢复成功")
                    return True

                # 仍超限，用压缩后的结果继续下一级
                messages[:] = new_msgs

            except Exception as e:
                logger.warning(f"[force_compact] {name} 压缩异常: {e}")
                messages[:] = pruned

            logger.info(f"[force_compact] {name} 仍超限，升级到下一级")

        logger.error("[force_compact] 所有级别耗尽，上下文仍超限")
        return False
