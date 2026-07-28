from __future__ import annotations

from enum import StrEnum, auto
from typing import Annotated, ClassVar, Literal, cast

from pydantic import BaseModel, Field, JsonValue

from vibe.app_server._model import ProtocolModel
from vibe.questions import UserQuestionRequest
from vibe.utils.tool_presentation import EffectCallDisplay, ToolEffectKind


class ShellEffectInput(ProtocolModel):
    command: str


class ShellEffectOutput(ProtocolModel):
    stdout: str
    stderr: str


class FileEditEffectInput(ProtocolModel):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class FileEditEffectOccurrence(ProtocolModel):
    start_line: int | None = None
    old_text: str
    new_text: str


class FileEditEffectOutput(ProtocolModel):
    file: str
    old_string: str
    new_string: str
    occurrences: list[FileEditEffectOccurrence] = Field(default_factory=list)


class FileSearchEffectInput(ProtocolModel):
    pattern: str
    path: str = "."
    max_matches: int | None = None


class FileSearchEffectMatch(ProtocolModel):
    path: str
    line: int | None = None


class FileSearchEffectOutput(ProtocolModel):
    matches: str
    match_count: int
    was_truncated: bool
    parsed_matches: list[FileSearchEffectMatch] = Field(default_factory=list)


class FileReadEffectInput(ProtocolModel):
    DEFAULT_LIMIT: ClassVar[int] = 2000

    file_path: str
    offset: int | None = None
    limit: int = DEFAULT_LIMIT


class FileReadEffectOutput(ProtocolModel):
    file_path: str
    content: str
    num_lines: int
    start_line: int
    requested_offset: int | None = None
    requested_limit: int = FileReadEffectInput.DEFAULT_LIMIT
    total_lines: int | None = None
    was_truncated: bool = False


class TodoEffectStatus(StrEnum):
    PENDING = auto()
    IN_PROGRESS = auto()
    COMPLETED = auto()
    CANCELLED = auto()


class TodoEffectPriority(StrEnum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


class TodoEffectItem(ProtocolModel):
    id: str
    content: str
    status: TodoEffectStatus = TodoEffectStatus.PENDING
    priority: TodoEffectPriority = TodoEffectPriority.MEDIUM


class TodoEffectInput(ProtocolModel):
    action: str
    todos: list[TodoEffectItem] | None = None


class TodoEffectOutput(ProtocolModel):
    todos: list[TodoEffectItem] = Field(default_factory=list)


class FileWriteEffectInput(ProtocolModel):
    file_path: str
    content: str


class FileWriteEffectOutput(ProtocolModel):
    file_path: str
    content: str


class WebSearchEffectInput(ProtocolModel):
    query: str


class WebSearchEffectSource(ProtocolModel):
    title: str
    url: str


class WebSearchEffectOutput(ProtocolModel):
    query: str
    answer: str
    sources: list[WebSearchEffectSource] = Field(default_factory=list)


class WebFetchEffectInput(ProtocolModel):
    url: str
    timeout: int | None = None


class WebFetchEffectOutput(ProtocolModel):
    url: str
    content: str
    content_type: str
    was_truncated: bool = False


class SkillEffectInput(ProtocolModel):
    name: str


class SkillEffectOutput(ProtocolModel):
    name: str
    content: str
    skill_dir: str | None = None


class SubagentEffectInput(ProtocolModel):
    task: str
    agent: str


class SubagentEffectOutput(ProtocolModel):
    response: str
    turns_used: int
    completed: bool


class _EffectDetailBase(ProtocolModel):
    tool_name: str
    display: EffectCallDisplay


class GenericEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.TOOL] = ToolEffectKind.TOOL
    input: JsonValue = None


class ShellEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.SHELL] = ToolEffectKind.SHELL
    input: ShellEffectInput | None = None


class FileEditEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.FILE_EDIT] = ToolEffectKind.FILE_EDIT
    input: FileEditEffectInput | None = None


class FileSearchEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.FILE_SEARCH] = ToolEffectKind.FILE_SEARCH
    input: FileSearchEffectInput | None = None


class FileReadEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.FILE_READ] = ToolEffectKind.FILE_READ
    input: FileReadEffectInput | None = None


class TodoEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.TODO] = ToolEffectKind.TODO
    input: TodoEffectInput | None = None


class FileWriteEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.FILE_WRITE] = ToolEffectKind.FILE_WRITE
    input: FileWriteEffectInput | None = None


class UserQuestionEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.USER_QUESTION] = ToolEffectKind.USER_QUESTION
    input: UserQuestionRequest | None = None


class WebSearchEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.WEB_SEARCH] = ToolEffectKind.WEB_SEARCH
    input: WebSearchEffectInput | None = None


class WebFetchEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.WEB_FETCH] = ToolEffectKind.WEB_FETCH
    input: WebFetchEffectInput | None = None


class SkillEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.SKILL] = ToolEffectKind.SKILL
    input: SkillEffectInput | None = None


class SubagentEffectDetail(_EffectDetailBase):
    kind: Literal[ToolEffectKind.SUBAGENT] = ToolEffectKind.SUBAGENT
    input: SubagentEffectInput | None = None
    child_session_id: str | None = None


EffectDetail = Annotated[
    GenericEffectDetail
    | ShellEffectDetail
    | FileEditEffectDetail
    | FileSearchEffectDetail
    | FileReadEffectDetail
    | TodoEffectDetail
    | FileWriteEffectDetail
    | UserQuestionEffectDetail
    | WebSearchEffectDetail
    | WebFetchEffectDetail
    | SkillEffectDetail
    | SubagentEffectDetail,
    Field(discriminator="kind"),
]


def effect_input_json(detail: EffectDetail) -> JsonValue:
    value = detail.input
    if not isinstance(value, BaseModel):
        return value
    return cast(JsonValue, value.model_dump(mode="json", by_alias=True))
