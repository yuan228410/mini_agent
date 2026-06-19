"""子代理加载器：加载 subagents/ 目录下的代理定义"""
import re
from pathlib import Path

import yaml

from ..core.runtime_types import MetadataDict, SubagentListText, SubagentSpec


class SubagentLoader:
    specs: dict[str, SubagentSpec]

    """从 subagents/*.md 加载子代理定义。

    每个文件包含 YAML frontmatter 和正文 system prompt：
        ---
        name: researcher
        description: 信息检索员
        tools: run_command, web_fetch
        max_turns: 10
        ---
        正文作为 system prompt...
    """

    def __init__(self, subagents_dir: Path):
        self._dir = Path(subagents_dir)
        self.specs: dict[str, SubagentSpec] = {}
        self._load_all()

    @property
    def subagents_dir(self) -> Path:
        return self._dir

    def _load_all(self):
        if not self.subagents_dir.exists():
            return
        for f in sorted(self.subagents_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = meta.get("name", f.stem)
            self.specs[name] = {
                "name": name,
                "description": meta.get("description", ""),
                "system_prompt": body.strip(),
                "tool_names": [t.strip() for t in meta.get("tools", "").split(",") if t.strip()],
                "max_turns": int(meta.get("max_turns", 10)),
            }

    def _parse_frontmatter(self, text: str) -> tuple[MetadataDict, str]:
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            return {}, text
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            meta = {}
        return meta, match.group(2).strip()

    def list_specs(self) -> SubagentListText:
        if not self.specs:
            return "(no subagents)"
        lines = []
        for name, spec in self.specs.items():
            tools = ", ".join(spec["tool_names"])
            lines.append(f"  - {name}: {spec['description']} (tools: {tools})")
        return "\n".join(lines)

    def get(self, name: str) -> SubagentSpec | None:
        return self.specs.get(name)