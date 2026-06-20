"""子代理加载器：加载 subagents/ 目录下的代理定义"""
import re
from pathlib import Path

import yaml

from ..core.runtime_types import MetadataDict, SubagentCreateInput, SubagentListText, SubagentSpec


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
        self.reload()

    @property
    def subagents_dir(self) -> Path:
        return self._dir

    def reload(self) -> None:
        """Reload subagent specs from disk."""
        self.specs.clear()
        if not self.subagents_dir.exists():
            return
        for f in sorted(self.subagents_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            meta, body = self._parse_frontmatter(text)
            name = str(meta.get("name", f.stem))
            raw_tools = meta.get("tools", "")
            if isinstance(raw_tools, list):
                tool_names = [str(t).strip() for t in raw_tools if str(t).strip()]
            else:
                tool_names = [t.strip() for t in str(raw_tools).split(",") if t.strip()]
            try:
                max_turns = int(meta.get("max_turns", 10))
            except (TypeError, ValueError):
                max_turns = 10
            self.specs[name] = {
                "name": name,
                "description": str(meta.get("description", "")),
                "system_prompt": body.strip(),
                "tool_names": tool_names,
                "max_turns": max_turns,
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

    def has(self, name: str) -> bool:
        return name in self.specs

    def create(self, data: SubagentCreateInput) -> Path | None:
        meta = {
            "name": data["name"],
            "description": data["description"],
            "tools": data.get("tools", []),
            "max_turns": data["max_turns"],
        }
        frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
        md_content = "---\n" + frontmatter + "\n---\n\n" + data["prompt"] + "\n"
        dest = self.subagents_dir / f"{data['name']}.md"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md_content, encoding="utf-8")
        self.reload()
        return dest
