"""Typed history persistence boundaries.

The history database sits between runtime messages, SQLite rows, search results,
and plan artifact storage.  These aliases keep those shapes explicit instead of
letting every caller see an undifferentiated ``dict``.
"""
from __future__ import annotations

from typing import Any, TypeAlias, TypedDict

from ..core.runtime_types import MessageDict, MetadataDict

HistoryMetadata: TypeAlias = MetadataDict
HistoryRuntimeMessage: TypeAlias = MessageDict
HistoryPlanArtifact: TypeAlias = dict[str, Any]
HistoryAsyncStats: TypeAlias = dict[str, int | bool]


class HistoryStorageRow(TypedDict):
    """Normalized row payload ready for insertion into the messages table."""

    role: str
    content: Any
    metadata: str


class HistorySearchRow(TypedDict):
    """Search/listing result returned by history search APIs."""

    id: int
    workspace: str
    session_id: str
    ts: str
    role: str
    content: str | None


class HistorySessionSummary(TypedDict):
    """Session list item returned by HistoryDB.list_sessions."""

    workspace: str
    session_id: str
    message_count: int
    updated_at: str | None


class HistoryReviewRow(TypedDict):
    """Compact message row used by history-review/delete screens."""

    id: int
    role: str
    content: str
    ts: str


class HistoryPoolStats(TypedDict):
    """Connection-pool statistics exposed by HistoryDBPool.stats."""

    total_connections: int
    max_connections: int
    users: list[str]
