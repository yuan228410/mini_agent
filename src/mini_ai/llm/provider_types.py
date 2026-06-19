"""Provider adapter wire aliases.

Provider modules translate core runtime messages/tools into OpenAI/Anthropic wire
payloads.  These aliases make that boundary explicit while preserving the current
JSON-dict transport format used by the HTTP clients.
"""
from __future__ import annotations

from typing import Any

from ..core.runtime_types import MessageDict, ToolDefinition

ProviderPayload = dict[str, Any]
ProviderMessage = MessageDict
ProviderToolDefinition = ToolDefinition
ProviderStreamChunk = dict[str, Any]
ToolCallBuffer = dict[str, Any]
AnthropicContentBlock = dict[str, Any]
