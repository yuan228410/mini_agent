"""Tool definition metadata and normalization utilities."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolMetadata:
    """Execution metadata used by schedulers and policy filters."""

    parallel_safe: bool = False
    cacheable: bool = False
    side_effect_free: bool = False
    allowed_in_plan: bool = False
    allowed_for_teammate: bool = False
    capabilities: tuple[str, ...] = field(default_factory=tuple)


DEFAULT_TOOL_METADATA: dict[str, ToolMetadata] = {
    "read_file": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("filesystem.read",)),
    "read_image": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("filesystem.read",)),
    "search_files": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("filesystem.read",)),
    "list_dir": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("filesystem.read",)),
    "web_fetch": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("network.http",)),
    "list_skills": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("skills.read",)),
    "load_skill": ToolMetadata(parallel_safe=True, cacheable=True, side_effect_free=True, allowed_in_plan=True, capabilities=("skills.read",)),
    "recall": ToolMetadata(parallel_safe=True, cacheable=False, side_effect_free=True, allowed_in_plan=True, capabilities=("memory.read",)),
    "search_history": ToolMetadata(parallel_safe=True, cacheable=False, side_effect_free=True, allowed_in_plan=True, capabilities=("history.read",)),
    "dispatch_subagent": ToolMetadata(parallel_safe=True, cacheable=False, side_effect_free=False, capabilities=("agent.spawn",)),
    "spawn_teammate": ToolMetadata(parallel_safe=True, cacheable=False, side_effect_free=False, capabilities=("team.spawn",)),
}


def metadata_for(name: str, explicit: ToolMetadata | dict[str, Any] | None = None) -> ToolMetadata:
    if isinstance(explicit, ToolMetadata):
        return explicit
    base = DEFAULT_TOOL_METADATA.get(name, ToolMetadata())
    if explicit:
        values = base.__dict__ | dict(explicit)
        if isinstance(values.get("capabilities"), list):
            values["capabilities"] = tuple(values["capabilities"])
        return ToolMetadata(**values)
    return base


def normalize_tool_definition(definition: dict, metadata: ToolMetadata | dict[str, Any] | None = None) -> dict:
    """Return a sanitized OpenAI-style tool definition with controlled metadata."""
    fn = definition.get("function") or {}
    name = fn.get("name") or definition.get("name") or ""
    if not name:
        raise ValueError("tool definition missing function.name")
    parameters = copy.deepcopy(fn.get("parameters") or {})
    if parameters.get("type") not in (None, "object"):
        raise ValueError(f"tool {name} parameters.type must be object")
    parameters.setdefault("type", "object")
    parameters.setdefault("properties", {})
    parameters.setdefault("required", [])
    meta = metadata_for(name, metadata or definition.get("metadata"))
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": fn.get("description") or definition.get("description") or "",
            "parameters": parameters,
        },
        "metadata": {
            "parallel_safe": meta.parallel_safe,
            "cacheable": meta.cacheable,
            "side_effect_free": meta.side_effect_free,
            "allowed_in_plan": meta.allowed_in_plan,
            "allowed_for_teammate": meta.allowed_for_teammate,
            "capabilities": list(meta.capabilities),
        },
    }
