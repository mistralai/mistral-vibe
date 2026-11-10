from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    SerializerFunctionWrapHandler,
    StrictInt,
    model_serializer,
)

from vibe.app_server._model import ProtocolModel
from vibe.utils import AgentEntrypoint
from vibe.utils.terminal import TerminalEmulator


class ClientInfo(ProtocolModel):
    name: str
    version: str
    title: str | None = None
    entrypoint: AgentEntrypoint = "unknown"
    terminal_emulator: TerminalEmulator = TerminalEmulator.UNKNOWN


type CallbackKind = Literal["approval", "user_input", "connector_auth"]
type ClientToolCapability = Literal[
    "filesystem/read", "filesystem/write", "terminal", "tools/call"
]
type TransportKind = Literal["in_process", "stdio"]


class ClientToolMethod(StrEnum):
    READ_TEXT_FILE = "clientTool/readTextFile"
    WRITE_TEXT_FILE = "clientTool/writeTextFile"
    TERMINAL_CREATE = "clientTool/terminal/create"
    TERMINAL_WAIT = "clientTool/terminal/wait"
    TERMINAL_OUTPUT = "clientTool/terminal/output"
    TERMINAL_KILL = "clientTool/terminal/kill"
    TERMINAL_RELEASE = "clientTool/terminal/release"


type PlatformCredentialsKind = Literal["oauth_access_token", "oidc_id_token"]


class PlatformCredentialsCapability(ProtocolModel):
    version: Literal[1]
    kind: PlatformCredentialsKind


class ClientToolDefinitionParams(ProtocolModel):
    name: Annotated[str, Field(min_length=1)]
    namespace: Annotated[str, Field(min_length=1)]
    description: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None


class AgentPlugin(ProtocolModel):
    name: Annotated[str, Field(min_length=1)]
    root: Annotated[str, Field(min_length=1)]


class ClientToolCallerParams(ProtocolModel):
    session_id: str
    root_session_id: str
    parent_session_id: str | None = None
    name: str | None = None
    agent_type: str | None = None


class ClientToolCallParams(ProtocolModel):
    call_id: str
    name: str
    input: JsonValue
    tool_call_id: str
    caller: ClientToolCallerParams

    @model_serializer(mode="wrap")
    def serialize_call(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, JsonValue]:
        serialized = handler(self)
        # Null is a valid required input, not an absent optional parameter.
        if self.input is None:
            serialized["input"] = None
        return serialized


class ClientToolCallError(ProtocolModel):
    message: str
    code: str | None = None
    details: JsonValue = None


class ClientToolCallResponse(ProtocolModel):
    output: JsonValue = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)
    error: ClientToolCallError | None = None


class ClientCapabilities(ProtocolModel):
    # Unknown capabilities are ignored, so a client can declare one ahead of
    # the servers that use it.
    model_config = ConfigDict(extra="ignore")

    callback_kinds: list[CallbackKind] = Field(default_factory=list)
    client_tools: list[ClientToolCapability] = Field(default_factory=list)
    session_spawn: bool = False
    session_peers: bool = False
    session_peer_restore: bool = False
    session_peer_mark_seen: bool = False
    agent_worktrees: bool = False
    disabled_notifications: list[str] = Field(default_factory=list)
    platform_credentials: PlatformCredentialsCapability | None = None
    agent_plugins: list[AgentPlugin] = Field(default_factory=list)
    client_tool_definitions: list[ClientToolDefinitionParams] = Field(
        default_factory=list
    )


class InitializeParams(ProtocolModel):
    client_info: ClientInfo
    capabilities: ClientCapabilities = Field(default_factory=ClientCapabilities)


class ServerInfo(ProtocolModel):
    name: str
    version: str


class ClientToolReadTextFileParams(ProtocolModel):
    session_id: str
    path: str
    line: Annotated[StrictInt, Field(ge=1)] | None = None
    limit: Annotated[StrictInt, Field(ge=1)] | None = None


class ClientToolReadTextFileResponse(ProtocolModel):
    content: str


class ClientToolWriteTextFileParams(ProtocolModel):
    session_id: str
    path: str
    content: str


class ClientToolTerminalCreateParams(ProtocolModel):
    session_id: str
    command: str
    args: list[str] | None = None
    env: dict[str, str] | None = None
    cwd: str
    output_byte_limit: Annotated[StrictInt, Field(gt=0)]
    tool_call_id: str | None = None


class ClientToolTerminalCreateResponse(ProtocolModel):
    terminal_id: str


class ClientToolTerminalParams(ProtocolModel):
    session_id: str
    terminal_id: str


class ClientToolTerminalWaitResponse(ProtocolModel):
    exit_code: StrictInt | None = None
    signal: str | None = None


class ClientToolTerminalOutputResponse(ProtocolModel):
    output: str
    truncated: bool


class InitializeResponse(ProtocolModel):
    server_info: ServerInfo
    process_id: int | None = None


class PlatformCredentialsResolveParams(ProtocolModel):
    audience: Annotated[str, Field(min_length=1)]
    minimum_validity_ms: Annotated[StrictInt, Field(ge=0, le=300_000)]
    rejected_revision: Annotated[str, Field(min_length=1)] | None = None


class PlatformCredentialsSnapshot(ProtocolModel):
    status: Literal["ready"]
    kind: PlatformCredentialsKind
    token: Annotated[str, Field(min_length=1, repr=False)]
    audience: Annotated[str, Field(min_length=1)]
    expires_at: Annotated[StrictInt, Field(gt=0, le=253_402_300_799_999)]
    revision: Annotated[str, Field(min_length=1)]


class PlatformCredentialsUnavailable(ProtocolModel):
    status: Literal["unavailable"]
    reason: Literal[
        "missing",
        "reauthentication_required",
        "transient",
        "invalid",
        "unauthorized_after_refresh",
    ]


type PlatformCredentialsResolveResponse = (
    PlatformCredentialsSnapshot | PlatformCredentialsUnavailable
)


class PlatformCredentialsInvalidatedParams(ProtocolModel):
    reason: Literal["logout", "account_changed", "workspace_changed"]
