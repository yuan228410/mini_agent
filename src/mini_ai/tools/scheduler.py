"""Tool-call scheduling helpers.

This module plans execution order only.  It does not execute tools, mutate messages,
or know about displays/persistence, keeping scheduling policy separate from the
registry implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..core.tool_models import ToolCall


@dataclass(frozen=True, slots=True)
class ToolCallSegment:
    calls: tuple[ToolCall, ...]
    parallel: bool = False


def plan_tool_call_segments(calls: Iterable[ToolCall], is_parallel_safe: Callable[[str], bool]) -> list[ToolCallSegment]:
    """Group calls into ordered serial/parallel execution segments.

    Consecutive parallel-safe calls are grouped into one segment.  Non-parallel
    calls become serial barriers.  The caller remains responsible for executing
    every segment in returned order.
    """

    segments: list[ToolCallSegment] = []
    parallel_segment: list[ToolCall] = []

    def flush_parallel_segment() -> None:
        if not parallel_segment:
            return
        segment = tuple(parallel_segment)
        parallel_segment.clear()
        segments.append(ToolCallSegment(calls=segment, parallel=len(segment) > 1))

    for call in calls:
        if is_parallel_safe(call.function.name):
            parallel_segment.append(call)
            continue
        flush_parallel_segment()
        segments.append(ToolCallSegment(calls=(call,), parallel=False))

    flush_parallel_segment()
    return segments
