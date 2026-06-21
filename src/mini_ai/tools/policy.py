"""Tool execution policies.

Policies live outside individual tool implementations so capability, safety and
scheduling decisions can evolve independently from the tool catalog.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from enum import StrEnum


class CommandRisk(StrEnum):
    SAFE = "safe"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class CommandPolicyVerdict:
    risk: CommandRisk
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.risk is CommandRisk.SAFE


_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\brm\s+[^\n;|&]*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b"), "递归强制删除命令被拒绝"),
    (re.compile(r"\brm\s+[^\n;|&]*(--recursive\b[^\n;|&]*--force\b|--force\b[^\n;|&]*--recursive\b)"), "递归强制删除命令被拒绝"),
    (re.compile(r"\bsudo\s+rm\b"), "sudo 删除命令被拒绝"),
    (re.compile(r"\b(?:mkfs|diskutil\s+erase\w*|fdisk|dd\s+)\b"), "磁盘/分区破坏性命令被拒绝"),
    (re.compile(r"\bgit\s+push\b[^\n;|&]*\s--force(?:-with-lease)?\b"), "强制推送命令被拒绝"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "硬重置命令被拒绝"),
    (re.compile(r"\bchmod\s+-r\s+777\b"), "递归放开权限命令被拒绝"),
    (re.compile(r"\bchown\s+-r\b"), "递归修改属主命令被拒绝"),
)


def _looks_like_shell_truncation(command: str) -> bool:
    # Common destructive redirection patterns, especially against absolute paths
    # or shell startup/config files.
    return bool(re.search(r">\s*(/|~|\.\./|\.?/?(?:\.zshrc|\.bashrc|\.profile))", command))


def classify_command(command: str) -> CommandPolicyVerdict:
    """Classify a shell command before execution.

    This intentionally rejects a conservative destructive subset.  It is not a
    shell parser and should be treated as a guardrail, not a sandbox.
    """

    normalized = " ".join(command.strip().split())
    if not normalized:
        return CommandPolicyVerdict(CommandRisk.DENY, "空命令被拒绝")

    lowered = normalized.lower()
    for pattern, reason in _DESTRUCTIVE_PATTERNS:
        if pattern.search(lowered):
            return CommandPolicyVerdict(CommandRisk.DENY, reason)

    if _looks_like_shell_truncation(normalized):
        return CommandPolicyVerdict(CommandRisk.DENY, "疑似覆盖关键路径的重定向命令被拒绝")

    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = []
    if tokens[:2] == ["rm", "-rf"] and len(tokens) >= 3:
        return CommandPolicyVerdict(CommandRisk.DENY, "递归强制删除命令被拒绝")

    return CommandPolicyVerdict(CommandRisk.SAFE)


def enforce_command_policy(command: str) -> CommandPolicyVerdict:
    return classify_command(command)
