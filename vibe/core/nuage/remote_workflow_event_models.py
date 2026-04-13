from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class BaseUIState(BaseModel):
    toolCallId: str = ""


class FileOperation(BaseModel):
    type: str = ""
    uri: str = ""
    content: str = ""


class FileUIState(BaseUIState):
    type: Literal["file"] = "file"
    operations: list[FileOperation] = []


class CommandResult(BaseModel):
    status: str = ""
    output: str = ""


class CommandUIState(BaseUIState):
    type: Literal["command"] = "command"
    command: str = ""
    result: CommandResult | None = None


class GenericToolResult(BaseModel):
    status: str = ""
    error: str | None = None


class GenericToolUIState(BaseUIState):
    model_config = ConfigDict(extra="allow")
    type: Literal["generic_tool"] = "generic_tool"
    arguments: dict[str, Any] = {}
    result: GenericToolResult | None = None


AnyToolUIState = FileUIState | CommandUIState | GenericToolUIState


def parse_tool_ui_state(raw: dict[str, Any]) -> AnyToolUIState | None:
    ui_type = raw.get("type")
    if ui_type == "file":
        return FileUIState.model_validate(raw)
    if ui_type == "command":
        return CommandUIState.model_validate(raw)
    if ui_type == "generic_tool":
        return GenericToolUIState.model_validate(raw)
    return None


class WorkingState(BaseModel):
    title: str = ""
    content: str = ""
    type: str = ""
    toolUIState: dict[str, Any] | None = None


class ContentChunk(BaseModel):
    type: str = ""
    text: str = ""


class AssistantMessageState(BaseModel):
    contentChunks: list[ContentChunk] = []


class AgentToolCallState(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str = ""
    tool_call_id: str = ""
    kwargs: dict[str, Any] = {}
    output: Any = None


class MessageSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    examples: list[Any] = []


class InputSchemaProperties(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: MessageSchema | None = None


class InputSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    properties: InputSchemaProperties | None = None


class PredefinedAnswersState(BaseModel):
    model_config = ConfigDict(extra="allow")
    input_schema: InputSchema | None = None


class WaitForInputInput(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: Any = None


class WaitForInputPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    input: WaitForInputInput | None = None


class AskUserQuestion(BaseModel):
    question: str


class AskUserQuestionArgs(BaseModel):
    model_config = ConfigDict(extra="allow")
    questions: list[AskUserQuestion] = []


class PendingInputRequest(BaseModel):
    task_id: str
    input_schema: dict[str, Any]
    label: str | None = None


class RemoteToolArgs(BaseModel):
    model_config = ConfigDict(extra="allow")
    summary: str | None = None
    content: str | None = None


class RemoteToolResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: str | None = None
