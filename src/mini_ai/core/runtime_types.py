"""Shared runtime boundary protocols and wire aliases.

These types keep the core runtime/session layer explicit without importing concrete
adapter implementations.  The runtime still uses dict-based wire payloads at the
outer LLM/persistence boundaries; code should use these aliases rather than bare
``dict`` so those boundaries are visible and easy to replace with DTOs later.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, overload

if TYPE_CHECKING:
    from .display_protocol import DisplayProtocol

MessageDict = dict[str, Any]
ToolDefinition = dict[str, Any]
ToolArgs = dict[str, Any]
ToolParameterSchema = dict[str, Any]
ToolWirePayload = dict[str, Any]
ToolFunctionPayload = dict[str, Any]
UsageDict = dict[str, int | float | str | bool | None]
MetadataDict = dict[str, Any]
ConfigDict = dict[str, Any]
RawConfigDict = ConfigDict
ModelConfigDict = ConfigDict
TimeoutConfigDict = ConfigDict
RunnerConfigDict = ConfigDict
DisplayConfigDict = ConfigDict
ToolConfigDict = ConfigDict
TeamConfigSettingsDict = ConfigDict
WorkflowConfigDict = ConfigDict
WebConfigDict = ConfigDict
McpConfigDict = ConfigDict
ImageConfigDict = ConfigDict
DatabaseHistoryConfigDict = ConfigDict
DatabaseConfigDict = ConfigDict
DisplayEventPayload = dict[str, Any]
DisplayWireEvent = dict[str, Any]
HistoryContent = str | list[Any]
PlanArtifactDict = dict[str, Any]
PlanStateValue = str
SubagentListText = str
TeamListText = str


class SubagentSpec(TypedDict):
    name: str
    description: str
    system_prompt: str
    tool_names: list[str]
    max_turns: int


class SubagentCreateInput(TypedDict):
    name: str
    description: str
    prompt: str
    tools: list[str]
    max_turns: int


InboxMessageTypeValue = Literal["message", "broadcast", "shutdown_request", "shutdown_response", "task_handoff"]
InboxMessageDict = TypedDict(
    "InboxMessageDict",
    {"type": InboxMessageTypeValue, "from": str, "content": str, "timestamp": float},
    total=False,
)


TeamMemberStatus = Literal["idle", "working", "offline", "shutdown"]
ACTIVE_TEAM_MEMBER_STATUSES: tuple[TeamMemberStatus, ...] = ("idle", "working")


class TeamMemberSummary(TypedDict):
    name: str
    role: str
    status: TeamMemberStatus


class TeamConfigDict(TypedDict):
    team_name: str
    members: list[TeamMemberSummary]


class TeamStatusResponse(TypedDict):
    teammates: list[TeamMemberSummary]
    has_team: bool


class BlackboardEntryDict(TypedDict):
    value: str
    author: str
    ts: float


BlackboardTextSnapshot = dict[str, str]
BlackboardDetailedSnapshot = dict[str, BlackboardEntryDict]


WorkflowTaskInput = TypedDict(
    "WorkflowTaskInput",
    {
        "id": str,
        "agent": str,
        "prompt": str,
        "depends_on": list[str],
        "condition": str,
        "max_retry": int,
        "timeout": int,
    },
    total=False,
)


class WorkflowTaskInfoDict(TypedDict):
    id: str
    agent: str
    prompt: str
    depends_on: list[str]


class WorkflowTaskStartDict(TypedDict):
    id: str
    agent: str
    prompt: str


WorkflowTaskEndDict = TypedDict(
    "WorkflowTaskEndDict",
    {"id": str, "status": str, "result_preview": str, "error": str},
    total=False,
)


class SessionComponents(TypedDict, total=False):
    store: MemoryStoreProtocol
    history_db: HistoryDBProtocol
    compactor: CompactorProtocol
    ctx_builder: ContextBuilderProtocol
    project_path: str
    skill_loader: SkillLoaderProtocol
    bus: MessageBusProtocol | None
    team_mgr: TeamManagerProtocol | None
    blackboard: BlackboardProtocol | None


class TeamComponents(TypedDict, total=False):
    bus: MessageBusProtocol
    team_mgr: TeamManagerProtocol
    blackboard: BlackboardProtocol


class Closable(Protocol):
    def close(self) -> None: ...


class RequestContextProtocol(Closable, Protocol):
    model_config: ModelConfigDict
    display: DisplayProtocol | None


class ToolRegistryProtocol(Protocol):
    def get_definitions(self) -> list[ToolDefinition]: ...

    def handle_tool_calls(
        self,
        msg: MessageDict,
        messages: list[MessageDict],
        display: DisplayProtocol | None = None,
        persist_fn=None,
    ) -> bool: ...

    def dispatch(self, name: str, args: ToolArgs) -> str | None: ...


class HistoryDBProtocol(Protocol):
    def append(self, workspace: str, session_id: str, role: str, content: HistoryContent, metadata: str = "") -> int | None: ...
    def load_session(self, workspace: str, session_id: str, limit: int = 0) -> list[MessageDict]: ...
    def save_plan(self, workspace: str, session_id: str, artifact: PlanArtifactDict) -> None: ...
    def get_current_plan(self, workspace: str, session_id: str) -> PlanArtifactDict | None: ...
    def list_plans(self, workspace: str, session_id: str) -> list[PlanArtifactDict]: ...
    def mark_plan_status(self, workspace: str, session_id: str, plan_id: str, status: PlanStateValue) -> None: ...


class MemoryStoreProtocol(Protocol):
    def get_tier_dir(self, level: str) -> Path | None: ...
    def tier_paths(self) -> list[Path]: ...
    def read_memory(self) -> str: ...
    def write_memory_at(self, content: str, level: str) -> None: ...


class SkillLoaderProtocol(Protocol):
    skills: dict[str, dict]
    skills_dir: Path

    def reload(self) -> None: ...
    def tier_paths(self) -> list[tuple[str, Path]]: ...
    def get_tier_dir(self, level: str) -> Path | None: ...
    def delete_skill(self, name: str) -> str: ...
    def delete_skill_at(self, name: str, level: str) -> str: ...
    def get_descriptions(self) -> str: ...
    def get_content(self, name: str) -> str: ...


class SubagentLoaderProtocol(Protocol):
    def reload(self) -> None: ...
    def list_specs(self) -> SubagentListText: ...
    def get(self, name: str) -> SubagentSpec | None: ...
    def has(self, name: str) -> bool: ...
    def create(self, data: SubagentCreateInput) -> Path | None: ...


class McpLoaderProtocol(Protocol):
    def get_tool_modules(self) -> list[object]: ...


class MessageBusProtocol(Protocol):
    def read_inbox(self, name: str, peek: bool = False) -> list[InboxMessageDict]: ...
    def send(self, sender: str, to: str, content: str, msg_type: InboxMessageTypeValue = "message") -> str: ...
    def broadcast(self, sender: str, content: str, teammates: list[str]) -> str: ...


class TeamManagerProtocol(Protocol):
    def set_display(self, display: DisplayProtocol) -> None: ...
    def spawn(self, name: str, role: str, prompt: str = "", *, derived_agent_resources: object | None = None) -> str: ...
    def list_all(self) -> TeamListText: ...
    def member_summaries(self) -> list[TeamMemberSummary]: ...
    def member_names(self) -> list[str]: ...
    def active_member_names(self) -> list[str]: ...
    def has_working_members(self) -> bool: ...
    def is_member_active(self, name: str) -> bool: ...
    def workspace_skills_dir(self) -> Path | None: ...


class BlackboardProtocol(Protocol):
    def put(self, key: str, value: str, author: str = "") -> str: ...
    def get(self, key: str, default: str | object = "") -> str | object: ...
    def list_keys(self, prefix: str = "") -> list[str]: ...

    @overload
    def snapshot(self, detailed: Literal[False] = False) -> BlackboardTextSnapshot: ...
    @overload
    def snapshot(self, detailed: Literal[True]) -> BlackboardDetailedSnapshot: ...
    def snapshot(self, detailed: bool = False) -> BlackboardTextSnapshot | BlackboardDetailedSnapshot: ...

    def clear(self) -> None: ...
    def wait_for_change(self, timeout: float = 5.0) -> bool: ...
    def render(self) -> str: ...


class CompactorProtocol(Protocol):
    def maybe_compact(self, messages: list[MessageDict], prompt_tokens: int, llm_chat, ctx, context_length: int) -> None: ...
    def compact(self, llm_chat, messages: list[MessageDict], ctx=None) -> list[MessageDict]: ...
    def force_compact(self, llm_chat, messages: list[MessageDict], ctx=None) -> bool: ...


class ContextBuilderProtocol(Protocol):
    def build(self, **kwargs) -> str: ...


class PlanStateStoreProtocol(Protocol):
    def get_plan_state(self, key: str): ...
    def set_plan_state(self, key: str, plan) -> None: ...


class ToolContextLike(Protocol):
    display: DisplayProtocol | None
    memory_store: MemoryStoreProtocol | None
    history_db: HistoryDBProtocol | None
    skill_loader: SkillLoaderProtocol | None
    subagent_loader: SubagentLoaderProtocol | None
    bus: MessageBusProtocol | None
    team_mgr: TeamManagerProtocol | None
    blackboard: BlackboardProtocol | None
    workflow_dirs: list[Path] | None
