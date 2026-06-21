"""Session-local tool catalog.

The catalog owns registration, definition normalization and name lookup.  Runtime
execution stays in ToolRegistry so scheduling, caching and display side effects do
not leak into the registration model.
"""
from __future__ import annotations

import copy
import json
from collections.abc import Callable

from .metadata import ToolMetadata, metadata_for, normalize_tool_definition
from ..core.runtime_types import ToolArgs, ToolDefinition


class BoundTool:
    """Small module-like wrapper binding a tool definition to a closure."""

    def __init__(self, definition: ToolDefinition, execute: Callable[[ToolArgs], object], metadata: ToolMetadata | dict | None = None):
        self.definition = copy.deepcopy(definition)
        self.execute = execute
        self.metadata = metadata


class ToolCatalog:
    def __init__(self, *, allowed_tools: set[str] | None = None) -> None:
        self.tools: list = []
        self.by_name: dict[str, object] = {}
        self.tool_metadata: dict[str, object] = {}
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        # Compatibility for tests/external extensions; new code should declare metadata.
        self.parallel_tools: set[str] = set()

    def add_tools(self, *modules) -> None:
        existing = {m.definition["function"]["name"] for m in self.tools}
        for module in modules:
            definition = self._normalize_module_definition(module)
            name = definition["function"]["name"]
            if name not in existing:
                self.tools.append(module)
                existing.add(name)
                continue
            for index, old in enumerate(self.tools):
                if old.definition["function"]["name"] == name:
                    self.tools[index] = module
                    break
        self.rebuild_index()

    def rebuild_index(self) -> None:
        self.by_name.clear()
        self.by_name.update({m.definition["function"]["name"]: m for m in self.tools})

    def definitions(self) -> list[ToolDefinition]:
        definitions = []
        for module in self.tools:
            name = module.definition["function"]["name"]
            if not self.is_allowed(name):
                continue
            definitions.append(json.loads(json.dumps(module.definition, ensure_ascii=False)))
        return definitions

    def get(self, name: str):
        return self.by_name.get(name)

    def is_parallel_safe(self, name: str) -> bool:
        return name in self.parallel_tools or bool(metadata_for(name, self.tool_metadata.get(name)).parallel_safe)

    def is_cacheable(self, name: str) -> bool:
        return bool(metadata_for(name, self.tool_metadata.get(name)).cacheable)

    def is_allowed(self, name: str) -> bool:
        return self.allowed_tools is None or name in self.allowed_tools

    def _normalize_module_definition(self, module) -> ToolDefinition:
        raw = module.definition() if callable(getattr(module, "definition", None)) else module.definition
        meta = metadata_for(raw.get("function", {}).get("name", ""), getattr(module, "metadata", None))
        normalized = normalize_tool_definition(raw, meta)
        module.definition = normalized
        self.tool_metadata[normalized["function"]["name"]] = meta
        return normalized
