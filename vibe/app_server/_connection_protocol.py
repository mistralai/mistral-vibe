from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StrictInt

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
type ClientToolCapability = Literal["filesystem/read", "filesystem/write", "terminal"]
type TransportKind = Literal["in_process", "stdio"]


class ClientToolMethod(StrEnum):
    READ_TEXT_FILE = "clientTool/readTextFile"
    WRITE_TEXT_FILE = "clientTool/writeTextFile"
    TERMINAL_CREATE = "clientTool/terminal/create"
    TERMINAL_WAIT = "clientTool/terminal/wait"
    TERMINAL_OUTPUT = "clientTool/terminal/output"
    TERMINAL_KILL = "clientTool/terminal/kill"
    TERMINAL_RELEASE = "clientTool/terminal/release"


class ClientCapabilities(ProtocolModel):
    callback_kinds: list[CallbackKind] = Field(default_factory=list)
    client_tools: list[ClientToolCapability] = Field(default_factory=list)
    disabled_notifications: list[str] = Field(default_factory=list)


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
