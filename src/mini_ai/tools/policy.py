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
    code: str = ""

    @property
    def allowed(self) -> bool:
        return self.risk is CommandRisk.SAFE

    def to_metadata(self) -> dict[str, str | bool]:
        return {"allowed": self.allowed, "risk": self.risk.value, "reason": self.reason, "code": self.code}


_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\brm\s+[^\n;|&]*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b"), "递归强制删除命令被拒绝", "recursive_force_delete"),
    (re.compile(r"\brm\s+[^\n;|&]*(--recursive\b[^\n;|&]*--force\b|--force\b[^\n;|&]*--recursive\b)"), "递归强制删除命令被拒绝", "recursive_force_delete"),
    (re.compile(r"\bsudo\s+rm\b"), "sudo 删除命令被拒绝", "sudo_delete"),
    (re.compile(r"\b(?:mkfs|diskutil\s+erase\w*|fdisk|dd\s+)\b"), "磁盘/分区破坏性命令被拒绝", "disk_destructive"),
    (re.compile(r"\bgit\s+push\b[^\n;|&]*\s--force(?:-with-lease)?\b"), "强制推送命令被拒绝", "git_force_push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "硬重置命令被拒绝", "git_hard_reset"),
    (re.compile(r"\bchmod\s+-r\s+777\b"), "递归放开权限命令被拒绝", "recursive_world_writable"),
    (re.compile(r"\bchown\s+-r\b"), "递归修改属主命令被拒绝", "recursive_chown"),
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
        return CommandPolicyVerdict(CommandRisk.DENY, "空命令被拒绝", "empty_command")

    lowered = normalized.lower()
    for pattern, reason, code in _DESTRUCTIVE_PATTERNS:
        if pattern.search(lowered):
            return CommandPolicyVerdict(CommandRisk.DENY, reason, code)

    if _looks_like_shell_truncation(normalized):
        return CommandPolicyVerdict(CommandRisk.DENY, "疑似覆盖关键路径的重定向命令被拒绝", "dangerous_redirection")

    try:
        tokens = shlex.split(normalized)
    except ValueError:
        tokens = []
    if tokens[:2] == ["rm", "-rf"] and len(tokens) >= 3:
        return CommandPolicyVerdict(CommandRisk.DENY, "递归强制删除命令被拒绝", "recursive_force_delete")

    return CommandPolicyVerdict(CommandRisk.SAFE, code="safe")


def enforce_command_policy(command: str) -> CommandPolicyVerdict:
    return classify_command(command)
