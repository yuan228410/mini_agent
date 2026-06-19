"""Explicit Web route request/response boundary aliases.

REST routes currently exchange JSON-compatible dict/list payloads with the frontend.
These aliases make each route contract visible without changing FastAPI validation or
filtering behavior at runtime.
"""
from __future__ import annotations

from typing import Any, TypeAlias

from ..core.runtime_types import DisplayWireEvent, PlanArtifactDict, TeamComponents

RoutePayload: TypeAlias = dict[str, Any]
RouteErrorResponse: TypeAlias = RoutePayload
RouteOkResponse: TypeAlias = RoutePayload

SessionMeta: TypeAlias = RoutePayload
SessionCreateRequest: TypeAlias = RoutePayload
SessionCreateResponse: TypeAlias = RoutePayload
SessionDeleteRequest: TypeAlias = RoutePayload
SessionBatchDeleteRequest: TypeAlias = RoutePayload
SessionBatchDeleteResponse: TypeAlias = RoutePayload
SessionRenameRequest: TypeAlias = RoutePayload
SessionRenameResponse: TypeAlias = RoutePayload
SessionListResponse: TypeAlias = RoutePayload
TodoItem: TypeAlias = RoutePayload
TodosResponse: TypeAlias = RoutePayload

WebSocketInboundPayload: TypeAlias = RoutePayload
ImageUpload: TypeAlias = RoutePayload
ChatHistoryImage: TypeAlias = RoutePayload
ChatHistoryEntry: TypeAlias = RoutePayload
ChatHistoryResponse: TypeAlias = RoutePayload
ChatResetRequest: TypeAlias = RoutePayload
ChatResetResponse: TypeAlias = RoutePayload

FileItem: TypeAlias = RoutePayload
BreadcrumbItem: TypeAlias = RoutePayload
FileListResponse: TypeAlias = RoutePayload
FileSearchResponse: TypeAlias = RoutePayload
BrowseDirItem: TypeAlias = RoutePayload
BrowseDirsResponse: TypeAlias = RoutePayload
FileReadTextResponse: TypeAlias = RoutePayload
FileReadBinaryResponse: TypeAlias = RoutePayload

ModelSummary: TypeAlias = RoutePayload
ModelsResponse: TypeAlias = RoutePayload
SwitchModelRequest: TypeAlias = RoutePayload
SwitchModelResponse: TypeAlias = RoutePayload

WorkspaceSummary: TypeAlias = RoutePayload
WorkspaceListResponse: TypeAlias = RoutePayload
WorkspaceCreateRequest: TypeAlias = RoutePayload
WorkspaceAddRequest: TypeAlias = RoutePayload
WorkspaceSwitchRequest: TypeAlias = RoutePayload
WorkspaceSwitchResponse: TypeAlias = RoutePayload
WorkspaceActionResponse: TypeAlias = RoutePayload
RemovedWorkspacesResponse: TypeAlias = RoutePayload
WorkspaceRestoreRequest: TypeAlias = RoutePayload

WebCommand: TypeAlias = RoutePayload
CommandsResponse: TypeAlias = RoutePayload
McpConfiguredServer: TypeAlias = RoutePayload
McpConnectedTool: TypeAlias = RoutePayload
McpConnectedServer: TypeAlias = RoutePayload
McpStatusResponse: TypeAlias = RoutePayload

ConfigResponse: TypeAlias = RoutePayload
SystemPromptResponse: TypeAlias = RoutePayload
ToolsResponse: TypeAlias = RoutePayload
SettingsResponse: TypeAlias = RoutePayload
SettingsUpdateRequest: TypeAlias = RoutePayload
SettingsUpdateResponse: TypeAlias = RoutePayload
AddModelRequest: TypeAlias = RoutePayload
AddModelResponse: TypeAlias = RoutePayload
RemoveModelRequest: TypeAlias = RoutePayload
RemoveModelResponse: TypeAlias = RoutePayload
McpServerAddRequest: TypeAlias = RoutePayload
McpServerAddResponse: TypeAlias = RoutePayload
McpServerRemoveResponse: TypeAlias = RoutePayload

SkillsListResponse: TypeAlias = RoutePayload
SkillInfoResponse: TypeAlias = RoutePayload
SkillLoadResponse: TypeAlias = RoutePayload
SkillInstallResponse: TypeAlias = RoutePayload
SkillCreateResponse: TypeAlias = RoutePayload
SkillDeleteResponse: TypeAlias = RoutePayload

BlackboardSnapshotResponse: TypeAlias = RoutePayload
DismissTeammateRequest: TypeAlias = RoutePayload
ClearBlackboardRequest: TypeAlias = RoutePayload
TeamActionResponse: TypeAlias = RoutePayload

__all__ = [
    "DisplayWireEvent",
    "PlanArtifactDict",
    "TeamComponents",
    "RoutePayload",
    "RouteErrorResponse",
    "RouteOkResponse",
    "SessionMeta",
    "SessionCreateRequest",
    "SessionCreateResponse",
    "SessionDeleteRequest",
    "SessionBatchDeleteRequest",
    "SessionBatchDeleteResponse",
    "SessionRenameRequest",
    "SessionRenameResponse",
    "SessionListResponse",
    "TodoItem",
    "TodosResponse",
    "WebSocketInboundPayload",
    "ImageUpload",
    "ChatHistoryImage",
    "ChatHistoryEntry",
    "ChatHistoryResponse",
    "ChatResetRequest",
    "ChatResetResponse",
    "FileItem",
    "BreadcrumbItem",
    "FileListResponse",
    "FileSearchResponse",
    "BrowseDirItem",
    "BrowseDirsResponse",
    "FileReadTextResponse",
    "FileReadBinaryResponse",
    "ModelSummary",
    "ModelsResponse",
    "SwitchModelRequest",
    "SwitchModelResponse",
    "WorkspaceSummary",
    "WorkspaceListResponse",
    "WorkspaceCreateRequest",
    "WorkspaceAddRequest",
    "WorkspaceSwitchRequest",
    "WorkspaceSwitchResponse",
    "WorkspaceActionResponse",
    "RemovedWorkspacesResponse",
    "WorkspaceRestoreRequest",
    "WebCommand",
    "CommandsResponse",
    "McpConfiguredServer",
    "McpConnectedTool",
    "McpConnectedServer",
    "McpStatusResponse",
    "ConfigResponse",
    "SystemPromptResponse",
    "ToolsResponse",
    "SettingsResponse",
    "SettingsUpdateRequest",
    "SettingsUpdateResponse",
    "AddModelRequest",
    "AddModelResponse",
    "RemoveModelRequest",
    "RemoveModelResponse",
    "McpServerAddRequest",
    "McpServerAddResponse",
    "McpServerRemoveResponse",
    "SkillsListResponse",
    "SkillInfoResponse",
    "SkillLoadResponse",
    "SkillInstallResponse",
    "SkillCreateResponse",
    "SkillDeleteResponse",
    "BlackboardSnapshotResponse",
    "DismissTeammateRequest",
    "ClearBlackboardRequest",
    "TeamActionResponse",
]
