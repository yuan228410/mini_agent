"""上下文裁剪器 — 三级策略裁剪旧工具结果，减少 prompt token

- protect_recent: 最近 N 轮 assistant 消息的工具结果完整保留
- soft_prune: 旧结果截断为首尾几行 + 省略号
- hard_prune: 很旧的结果替换为占位符

不修改原始 messages，返回裁剪后的新列表。
"""
from __future__ import annotations

from dataclasses import dataclass
from ..logger import logger


@dataclass
class PruneOptions:
    protect_recent: int = 3          # 最近 N 轮 assistant 的工具结果完整保留
    soft_prune_lines: int = 5        # 软裁剪保留首尾行数
    hard_prune_after: int = 10       # 超过 N 轮则硬裁剪
    max_tool_result_chars: int = 2000  # 超过此长度才触发软裁剪


class ContextPruner:
    """上下文裁剪器"""

    @staticmethod
    def prune(messages: list[dict], opts: PruneOptions | None = None) -> list[dict]:
        """裁剪消息历史中的工具结果，返回新列表

        增量优化：已裁剪的消息通过 _pruned 标记跳过，避免重复遍历。
        - _pruned="hard"：已硬裁剪（内容最短），任何参数都跳过
        - _pruned="soft"：已软裁剪，同级或更宽松参数跳过
        """
        if opts is None:
            opts = PruneOptions()

        if not messages:
            return messages

        # 1. 从末尾往前编号 assistant 消息（1=最近）
        assistant_count = 0
        msg_assistant_index: list[int] = [0] * len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "assistant":
                assistant_count += 1
                msg_assistant_index[i] = assistant_count

        # 2. 遍历消息，按轮次深度裁剪工具结果
        result: list[dict] = []
        hard_pruned = 0
        soft_pruned = 0
        skipped = 0  # 增量跳过计数

        for i, msg in enumerate(messages):
            role = msg.get("role")

            if role == "tool":
                # 增量优化：已硬裁剪 → 跳过（内容已最短）
                if msg.get("_pruned") == "hard":
                    result.append(msg)
                    skipped += 1
                    continue

                # 增量优化：已软裁剪且当前参数不比上次更激进 → 跳过
                if msg.get("_pruned") == "soft":
                    prev_level = msg.get("_prune_level", 0)
                    if opts.hard_prune_after >= prev_level:
                        result.append(msg)
                        skipped += 1
                        continue

                # 找前方最近的 assistant 消息的 depth
                nearest_depth = 0
                for j in range(i - 1, -1, -1):
                    if messages[j].get("role") == "assistant":
                        nearest_depth = msg_assistant_index[j]
                        break

                # 保护区内：完整保留
                if 0 < nearest_depth <= opts.protect_recent:
                    result.append(msg)
                    continue

                content = msg.get("content", "")
                if not isinstance(content, str):
                    result.append(msg)
                    continue

                # 硬裁剪：替换为占位符
                if nearest_depth > opts.hard_prune_after:
                    hard_pruned += 1
                    pruned = dict(msg)
                    pruned["content"] = "[tool result pruned]"
                    pruned["_pruned"] = "hard"
                    result.append(pruned)
                    continue

                # 软裁剪：首尾几行 + 省略号
                if len(content) > opts.max_tool_result_chars:
                    soft_pruned += 1
                    pruned = dict(msg)
                    pruned["content"] = _soft_prune(content, opts.soft_prune_lines)
                    pruned["_pruned"] = "soft"
                    pruned["_prune_level"] = opts.hard_prune_after
                    result.append(pruned)
                    continue

                result.append(msg)

            else:
                # system / user / assistant 消息：不裁剪
                result.append(msg)

        if hard_pruned or soft_pruned:
            logger.info(f"[context_pruner] 裁剪完成: hard={hard_pruned}, soft={soft_pruned}, skipped={skipped}, msgs={len(messages)}→{len(result)}")
        elif skipped:
            logger.debug(f"[context_pruner] 增量跳过: {skipped} 条已裁剪")
        else:
            logger.debug(f"[context_pruner] 无需裁剪: msgs={len(messages)}")

        return result


def _soft_prune(content: str, keep_lines: int) -> str:
    """软裁剪：保留首尾 N 行 + 省略号"""
    lines = content.split("\n")

    # 行数足够多时：按行裁剪（至少省略 3 行才值得）
    if len(lines) > keep_lines * 2 + 3:
        head = "\n".join(lines[:keep_lines])
        tail = "\n".join(lines[-keep_lines:])
        omitted = len(lines) - keep_lines * 2
        return f"{head}\n... ({omitted} lines omitted) ...\n{tail}"

    # 行数不多但字符超长：按字符截断
    keep_chars = keep_lines * 80
    if len(content) > keep_chars * 2:
        head = content[:keep_chars]
        tail = content[-keep_chars:]
        omitted = len(content) - keep_chars * 2
        return f"{head}\n... ({omitted} chars omitted) ...\n{tail}"

    # 内容短：不裁剪
    return content
