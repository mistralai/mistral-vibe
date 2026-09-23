from __future__ import annotations

from typing import Annotated, Any, Literal, Self, assert_never

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema

type JsonObject = dict[str, JsonValue]
type JsonSchema = bool | JsonObject
type RustHookPoint = Literal[
    "pre_agent_turn",
    "pre_llm_call",
    "post_llm_call",
    "post_agent_turn",
    "pre_tool_call",
    "post_tool_call",
]

_RESERVED_DIRECT_TOOL_NAMES = {"search_tool_functions", "run_typescript", "skill"}

RustRuntimeBuiltinToolName = Literal[
    "self.sleep",
    "file_system.read_file",
    "file_system.write_file",
    "file_system.search_replace",
    "file_system.bash",
    "skill.read",
    "process.start",
    "process.output",
    "process.write",
    "process.list",
    "process.stop",
    "subagent.list",
    "subagent.spawn",
    "subagent.wait",
    "subagent.send_message",
    "subagent.interrupt",
    "subagent.stop",
]

RUNTIME_BUILTIN_TOOL_NAMES: tuple[RustRuntimeBuiltinToolName, ...] = (
    "self.sleep",
    "file_system.read_file",
    "file_system.write_file",
    "file_system.search_replace",
    "file_system.bash",
    "skill.read",
    "process.start",
    "process.output",
    "process.write",
    "process.list",
    "process.stop",
    "subagent.list",
    "subagent.spawn",
    "subagent.wait",
    "subagent.send_message",
    "subagent.interrupt",
    "subagent.stop",
)

RUNTIME_BUILTIN_TOOL_NAMESPACES: frozenset[str] = frozenset(
    name.split(".", 1)[0] for name in RUNTIME_BUILTIN_TOOL_NAMES
)

# A provided call is gated as ``group.tool``, the shape a builtin is named by, and the
# permission resolver reads that one string: a ``skill`` group holding a ``read`` tool
# would be resolved against the rules for ``skill.read``.
_RESERVED_TOOL_GROUP_NAMES: frozenset[str] = RUNTIME_BUILTIN_TOOL_NAMESPACES | {"agent"}


class RustProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", serialize_by_alias=True)


class RustProvidedToolDefinition(RustProtocolModel):
    name: str
    description: str = ""
    input_schema: JsonSchema = Field(default_factory=dict)
    output_schema: JsonSchema | None = None
    exposure: Literal["programmatic", "direct", "direct_and_programmatic"]


class RustConnectorToolGroupMetadata(RustProtocolModel):
    type: Literal["connector"] = "connector"
    connector_id: str = Field(min_length=1)


class RustToolGroupDefinition(RustProtocolModel):
    name: str
    description: str = ""
    metadata: RustConnectorToolGroupMetadata | None = None
    icon_url: str | None = None
    tools: list[RustProvidedToolDefinition] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _is_programmatic_identifier(value):
            raise ValueError("tool group name must be a valid TypeScript identifier")
        if value in _RESERVED_TOOL_GROUP_NAMES:
            raise ValueError("tool group name is reserved")
        return value

    @model_validator(mode="after")
    def validate_tools(self) -> Self:
        names: set[str] = set()
        for tool in self.tools:
            if not _is_programmatic_identifier(tool.name):
                raise ValueError(
                    f"tool function {tool.name!r} must be a valid TypeScript identifier"
                )
            if tool.name in names:
                raise ValueError(f"duplicate tool function {self.name}.{tool.name}")
            names.add(tool.name)
        return self


class RustSkillDefinition(RustProtocolModel):
    name: str
    description: str = Field(min_length=1)
    path: str = Field(pattern=r"(?:^|[\\/])SKILL\.md$")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not _is_skill_name(value):
            raise ValueError(
                "skill name must be an Agent Skill name or a Runtime-qualified namespace:name alias"
            )
        return value

    @field_validator("description", "path")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class RustKnowledgeFolderDefinition(RustProtocolModel):
    name: str
    description: str
    path: str
    access: Literal["read_only", "read_write"]


class RustAgentTypeDefinition(RustProtocolModel):
    name: str
    description: str
    path: str


class RustHarnessHookToolKey(RustProtocolModel):
    target: Literal["self", "filesystem", "process", "provided", "skill", "subagent"]
    qualified_name: str = Field(min_length=1)


class RustAlwaysHookSelector(RustProtocolModel):
    type: Literal["always"] = "always"


class RustToolKeysHookSelector(RustProtocolModel):
    type: Literal["tool_keys"] = "tool_keys"
    tool_keys: list[RustHarnessHookToolKey] = Field(min_length=1)


type RustHarnessHookSelector = Annotated[
    RustAlwaysHookSelector | RustToolKeysHookSelector, Field(discriminator="type")
]


class RustHarnessHookBinding(RustProtocolModel):
    id: str = Field(min_length=1)
    point: RustHookPoint
    order: int = Field(ge=0)
    selector: RustHarnessHookSelector


class RustHarnessCapabilitySet(RustProtocolModel):
    tool_groups: list[RustToolGroupDefinition] = Field(default_factory=list)
    skills: list[RustSkillDefinition] = Field(default_factory=list)
    knowledge_folders: list[RustKnowledgeFolderDefinition] = Field(default_factory=list)
    agent_types: list[RustAgentTypeDefinition] = Field(default_factory=list)
    hook_bindings: list[RustHarnessHookBinding] = Field(default_factory=list)


class RustPluginContextDefinition(RustProtocolModel):
    name: str
    description: str
    path: str
    capabilities: RustHarnessCapabilitySet = Field(
        default_factory=RustHarnessCapabilitySet
    )


class RustDisabledRuntimeToolFeature(RustProtocolModel):
    mode: Literal["disabled"] = "disabled"


class RustEnabledRuntimeToolFeature(RustProtocolModel):
    mode: Literal["enabled"] = "enabled"


RustRuntimeToolFeature = Annotated[
    RustDisabledRuntimeToolFeature | RustEnabledRuntimeToolFeature,
    Field(discriminator="mode"),
]


class RustDisabledLargeOutputPolicy(RustProtocolModel):
    mode: Literal["disabled"] = "disabled"


class RustFilesystemLargeOutputPolicy(RustProtocolModel):
    mode: Literal["filesystem"] = "filesystem"
    max_output_tokens: Annotated[int, Field(gt=0)] | SkipJsonSchema[None] = Field(
        default_factory=lambda: None, exclude_if=lambda value: value is None
    )
    model_visible_output_tokens: Annotated[int, Field(gt=0)] | SkipJsonSchema[None] = (
        Field(default_factory=lambda: None, exclude_if=lambda value: value is None)
    )

    @field_validator("max_output_tokens", "model_visible_output_tokens", mode="before")
    @classmethod
    def reject_explicit_null_limits(cls, value: object) -> object:
        if value is None:
            raise ValueError("large-output limits must be omitted or positive integers")
        return value

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if (
            self.max_output_tokens is not None
            and self.model_visible_output_tokens is not None
            and self.model_visible_output_tokens > self.max_output_tokens
        ):
            raise ValueError(
                "model_visible_output_tokens must be less than or equal to max_output_tokens"
            )
        return self


RustLargeOutputPolicy = Annotated[
    RustDisabledLargeOutputPolicy | RustFilesystemLargeOutputPolicy,
    Field(discriminator="mode"),
]


class RustUnixCommandEnvironment(RustProtocolModel):
    mode: Literal["unix"] = "unix"


class RustGitBashCommandEnvironment(RustProtocolModel):
    mode: Literal["git_bash"] = "git_bash"


class RustPowerShellCommandEnvironment(RustProtocolModel):
    mode: Literal["powershell"] = "powershell"


class RustInMemoryBashCommandEnvironment(RustProtocolModel):
    mode: Literal["in_memory_bash"] = "in_memory_bash"


class RustDisabledCommandEnvironment(RustProtocolModel):
    mode: Literal["disabled"] = "disabled"


RustCommandEnvironment = Annotated[
    RustDisabledCommandEnvironment
    | RustUnixCommandEnvironment
    | RustGitBashCommandEnvironment
    | RustPowerShellCommandEnvironment
    | RustInMemoryBashCommandEnvironment,
    Field(discriminator="mode"),
]


class RustDisabledCompactionPolicy(RustProtocolModel):
    mode: Literal["disabled"] = "disabled"


class RustAutomaticCompactionPolicy(RustProtocolModel):
    mode: Literal["automatic"] = "automatic"
    token_threshold: int = Field(gt=0)


RustCompactionPolicy = Annotated[
    RustDisabledCompactionPolicy | RustAutomaticCompactionPolicy,
    Field(discriminator="mode"),
]


class RustImageDeliverySettings(RustProtocolModel):
    agent: Literal["native", "resource_link"] = "native"
    compaction: Literal["native", "resource_link"] = "native"


class RustTurnSettings(RustProtocolModel):
    max_iterations: Annotated[int, Field(gt=0)] | None = None


class RustContextSettings(RustProtocolModel):
    compaction: RustCompactionPolicy
    image_delivery: RustImageDeliverySettings = Field(
        default_factory=RustImageDeliverySettings
    )


class RustProgrammaticToolSettings(RustProtocolModel):
    max_effects: int = Field(gt=0)
    max_operations: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_limits(self) -> Self:
        if self.max_operations < self.max_effects:
            raise ValueError(
                "max_operations must be greater than or equal to max_effects"
            )
        return self


class RustToolSettings(RustProtocolModel):
    programmatic: RustProgrammaticToolSettings
    subagents: RustRuntimeToolFeature
    background_processes: RustRuntimeToolFeature
    command_environment: RustCommandEnvironment
    large_output: RustLargeOutputPolicy


class RustHarnessSettings(RustProtocolModel):
    turn: RustTurnSettings
    context: RustContextSettings
    tools: RustToolSettings


class RustHarnessConfig(RustProtocolModel):
    task_id: str
    system_instructions: str = ""
    settings: RustHarnessSettings
    capabilities: RustHarnessCapabilitySet = Field(
        default_factory=RustHarnessCapabilitySet
    )
    plugins: list[RustPluginContextDefinition] = Field(default_factory=list)

    @field_validator("task_id")
    @classmethod
    def validate_task_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("task_id must not be blank")
        return value

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        capability_sets = [
            self.capabilities,
            *(plugin.capabilities for plugin in self.plugins),
        ]
        tool_groups = [
            group
            for capabilities in capability_sets
            for group in capabilities.tool_groups
        ]
        skills = [
            skill for capabilities in capability_sets for skill in capabilities.skills
        ]
        _reject_duplicates("tool group name", [group.name for group in tool_groups])
        direct_names = [
            tool.name
            for group in tool_groups
            for tool in group.tools
            if tool.exposure in {"direct", "direct_and_programmatic"}
        ]
        _reject_duplicates("direct tool name", direct_names)
        reserved_direct_names = sorted(set(direct_names) & _RESERVED_DIRECT_TOOL_NAMES)
        if reserved_direct_names:
            raise ValueError(
                "direct tool names conflict with built-in tools: "
                + ", ".join(repr(name) for name in reserved_direct_names)
            )
        _reject_duplicates("skill name", [skill.name for skill in skills])
        _reject_duplicates("skill path", [skill.path for skill in skills])
        return self


class RustHarnessConfigUpdate(RustProtocolModel):
    system_instructions: str | None = None
    settings: RustHarnessSettings | None = None
    capabilities: RustHarnessCapabilitySet | None = None
    plugins: list[RustPluginContextDefinition] | None = None


class RustToolCall(RustProtocolModel):
    id: str
    name: str
    arguments: JsonObject = Field(default_factory=dict)
    argument_error: str | None = None


class RustContentAnnotations(RustProtocolModel):
    audience: list[Literal["user", "assistant"]] = Field(default_factory=list)
    priority: float | None = Field(default=None, ge=0, le=1)
    last_modified: str | None = Field(
        default=None,
        validation_alias=AliasChoices("last_modified", "lastModified"),
        serialization_alias="lastModified",
    )


class RustContentIcon(RustProtocolModel):
    src: str
    mime_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )
    sizes: list[str] = Field(default_factory=list)
    theme: Literal["light", "dark"] | None = None


class RustContentBlockMetadata(RustProtocolModel):
    annotations: RustContentAnnotations | None = None
    meta: JsonObject | None = Field(default=None, alias="_meta")


class RustTextContentBlock(RustContentBlockMetadata):
    type: Literal["text"] = "text"
    text: str


class RustImageContentBlock(RustContentBlockMetadata):
    type: Literal["image"] = "image"
    data: str
    mime_type: str = Field(
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )


class RustAudioContentBlock(RustContentBlockMetadata):
    type: Literal["audio"] = "audio"
    data: str
    mime_type: str = Field(
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )


class RustResourceLinkContentBlock(RustContentBlockMetadata):
    type: Literal["resource_link"] = "resource_link"
    uri: str
    name: str
    title: str | None = None
    description: str | None = None
    mime_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )
    size: int | None = Field(default=None, ge=0)
    icons: list[RustContentIcon] = Field(default_factory=list)


class RustTextResourceContents(RustProtocolModel):
    uri: str
    mime_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )
    text: str
    meta: JsonObject | None = Field(default=None, alias="_meta")


class RustBlobResourceContents(RustProtocolModel):
    uri: str
    mime_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mime_type", "mimeType"),
        serialization_alias="mimeType",
    )
    blob: str
    meta: JsonObject | None = Field(default=None, alias="_meta")


RustResourceContents = RustTextResourceContents | RustBlobResourceContents


class RustEmbeddedResourceContentBlock(RustContentBlockMetadata):
    type: Literal["resource"] = "resource"
    resource: RustResourceContents


RustContentBlock = Annotated[
    RustTextContentBlock
    | RustImageContentBlock
    | RustAudioContentBlock
    | RustResourceLinkContentBlock
    | RustEmbeddedResourceContentBlock,
    Field(discriminator="type"),
]


class RustJsonToolArguments(RustProtocolModel):
    type: Literal["json"] = "json"
    raw: str
    value: JsonValue


class RustInvalidJsonToolArguments(RustProtocolModel):
    type: Literal["invalid_json"] = "invalid_json"
    raw: str
    error: str


RustToolArguments = Annotated[
    RustJsonToolArguments | RustInvalidJsonToolArguments, Field(discriminator="type")
]

EMPTY_TOOL_ARGUMENTS = "{}"


def tool_call_wire_arguments(arguments: RustToolArguments) -> str:
    """Never send ``raw`` to a provider directly. A rejected tool call strands the session
    for good, because the assistant message is replayed from committed history every turn.
    """
    if isinstance(arguments, RustJsonToolArguments):
        return (
            arguments.raw if isinstance(arguments.value, dict) else EMPTY_TOOL_ARGUMENTS
        )
    if isinstance(arguments, RustInvalidJsonToolArguments):
        return EMPTY_TOOL_ARGUMENTS
    assert_never(arguments)


class RustReasoningTextContent(RustProtocolModel):
    type: Literal["text"] = "text"
    text: str


class RustReasoningSummaryContent(RustProtocolModel):
    type: Literal["summary"] = "summary"
    text: str


class RustReasoningRedactedContent(RustProtocolModel):
    type: Literal["redacted"] = "redacted"
    data: str


RustReasoningContent = Annotated[
    RustReasoningTextContent
    | RustReasoningSummaryContent
    | RustReasoningRedactedContent,
    Field(discriminator="type"),
]


class RustReasoningPart(RustProtocolModel):
    type: Literal["reasoning"] = "reasoning"
    content: list[RustReasoningContent] = Field(min_length=1)
    meta: JsonObject | None = Field(default=None, alias="_meta")


class RustModelToolCallPart(RustProtocolModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments: RustToolArguments
    meta: JsonObject | None = Field(default=None, alias="_meta")


RustAssistantPart = Annotated[
    RustTextContentBlock
    | RustImageContentBlock
    | RustAudioContentBlock
    | RustResourceLinkContentBlock
    | RustEmbeddedResourceContentBlock
    | RustReasoningPart
    | RustModelToolCallPart,
    Field(discriminator="type"),
]


class RustSystemMessage(RustProtocolModel):
    role: Literal["system"] = "system"
    content: list[RustTextContentBlock] = Field(min_length=1)


class RustUserMessage(RustProtocolModel):
    role: Literal["user"] = "user"
    content: list[RustContentBlock] = Field(min_length=1)


class RustAssistantMessage(RustProtocolModel):
    role: Literal["assistant"] = "assistant"
    content: list[RustAssistantPart] = Field(min_length=1)


class RustToolMessage(RustProtocolModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    outcome: Literal["success", "failure"]
    content: list[RustContentBlock] = Field(default_factory=list)
    meta: JsonObject | None = Field(default=None, alias="_meta")


RustMessage = Annotated[
    RustSystemMessage | RustUserMessage | RustAssistantMessage | RustToolMessage,
    Field(discriminator="role"),
]

RustHarnessConfig.model_rebuild()

type RustCompletionFinishReason = Literal[
    "stop", "tool_call", "length", "content_filter", "other"
]


class RustProtocolError(RustProtocolModel):
    code: str
    message: str
    retryable: bool
    details: JsonValue = None


class RustToolSuccessResult(RustProtocolModel):
    type: Literal["success"] = "success"
    content: list[RustContentBlock] = Field(default_factory=list)
    structured_content: JsonValue = None
    meta: JsonObject | None = Field(default=None, alias="_meta")

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> Any:
        serialized = handler(self)
        if "structured_content" not in self.model_fields_set:
            serialized.pop("structured_content", None)
        return serialized


class RustToolFailureResult(RustProtocolModel):
    type: Literal["failure"] = "failure"
    content: list[RustContentBlock] = Field(default_factory=list)
    structured_content: JsonValue = None
    meta: JsonObject | None = Field(default=None, alias="_meta")
    error: RustProtocolError

    @model_serializer(mode="wrap")
    def serialize_model(self, handler: Any) -> Any:
        serialized = handler(self)
        if "structured_content" not in self.model_fields_set:
            serialized.pop("structured_content", None)
        return serialized


RustToolResult = Annotated[
    RustToolSuccessResult | RustToolFailureResult, Field(discriminator="type")
]


class RustTokenUsage(RustProtocolModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens must equal input_tokens + output_tokens")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens must not exceed input_tokens")
        return self


class RustCompletionResultToolCallPart(RustProtocolModel):
    type: Literal["tool_call"] = "tool_call"
    id: str
    name: str
    arguments_json: str
    meta: JsonObject | None = Field(default=None, alias="_meta")


RustCompletionResultPart = Annotated[
    RustTextContentBlock
    | RustImageContentBlock
    | RustAudioContentBlock
    | RustResourceLinkContentBlock
    | RustEmbeddedResourceContentBlock
    | RustReasoningPart
    | RustCompletionResultToolCallPart,
    Field(discriminator="type"),
]


class RustCompletionResult(RustProtocolModel):
    parts: list[RustCompletionResultPart] = Field(min_length=1)
    finish_reason: RustCompletionFinishReason
    usage: RustTokenUsage | None = None


class RustCandidateMessage(RustAssistantMessage):
    pass


class RustCompletionCandidate(RustProtocolModel):
    message: RustCandidateMessage
    finish_reason: RustCompletionFinishReason
    usage: RustTokenUsage | None


class RustAcceptCandidate(RustProtocolModel):
    type: Literal["candidate"] = "candidate"


class RustReplaceAssistantContent(RustProtocolModel):
    type: Literal["replace_assistant_content"] = "replace_assistant_content"
    content: list[RustContentBlock] = Field(min_length=1)


RustAgentCompletionAcceptance = Annotated[
    RustAcceptCandidate | RustReplaceAssistantContent, Field(discriminator="type")
]


class RustToolDefinition(RustProtocolModel):
    name: str
    description: str
    parameters: JsonSchema


class RustModelMessageAppend(RustProtocolModel):
    type: Literal["append"] = "append"
    base_revision: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    messages: list[RustMessage] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        if self.revision < self.base_revision:
            raise ValueError("append message revision cannot precede its base revision")
        return self


class RustModelMessageReplace(RustProtocolModel):
    type: Literal["replace"] = "replace"
    revision: int = Field(default=0, ge=0)
    messages: list[RustMessage]


RustModelMessageUpdate = Annotated[
    RustModelMessageAppend | RustModelMessageReplace, Field(discriminator="type")
]


class RustModelToolCatalogKeep(RustProtocolModel):
    type: Literal["keep"] = "keep"
    revision: int = Field(default=0, ge=0)


class RustModelToolCatalogReplace(RustProtocolModel):
    type: Literal["replace"] = "replace"
    revision: int = Field(default=0, ge=0)
    tools: list[RustToolDefinition]


RustModelToolCatalogUpdate = Annotated[
    RustModelToolCatalogKeep | RustModelToolCatalogReplace, Field(discriminator="type")
]


class RustModelInputUpdate(RustProtocolModel):
    messages: RustModelMessageUpdate
    tool_catalog: RustModelToolCatalogUpdate


class RustRuntimeBuiltinToolCall(RustProtocolModel):
    type: Literal["runtime_builtin"] = "runtime_builtin"
    name: RustRuntimeBuiltinToolName
    arguments: JsonObject = Field(default_factory=dict)


class RustProvidedToolCall(RustProtocolModel):
    type: Literal["provided"] = "provided"
    group_name: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    arguments: JsonObject = Field(default_factory=dict)


RustExternalToolCall = Annotated[
    RustRuntimeBuiltinToolCall | RustProvidedToolCall, Field(discriminator="type")
]


class RustLLMCallAction(RustProtocolModel):
    type: Literal["llm_call"] = "llm_call"
    action_id: str
    turn_id: str | None
    purpose: Literal["agent", "compaction"] = "agent"
    compaction_id: str | None = None
    attempt: int | None = Field(default=None, gt=0)
    trigger: Literal["automatic", "manual"] | None = None
    iteration: int = Field(ge=0)
    max_iterations: Annotated[int, Field(gt=0)] | None
    model_input: RustModelInputUpdate

    @model_validator(mode="after")
    def validate_purpose(self) -> Self:
        if self.purpose == "agent":
            if self.turn_id is None:
                raise ValueError("agent LLM calls require a turn ID")
            if (
                self.trigger is not None
                or self.compaction_id is not None
                or self.attempt is not None
            ):
                raise ValueError("agent LLM calls cannot have compaction fields")
            return self
        if self.compaction_id is None or self.attempt is None:
            raise ValueError(
                "compaction LLM calls require compaction identity and attempt"
            )
        if self.trigger == "automatic" and self.turn_id is not None:
            return self
        if self.trigger == "manual" and self.turn_id is None:
            return self
        raise ValueError("compaction LLM call trigger and turn ID do not match")


class RustRuntimeBuiltinToolCallAction(RustProtocolModel):
    type: Literal["runtime_builtin_tool_call"] = "runtime_builtin_tool_call"
    action_id: str
    turn_id: str
    call_id: str
    call: RustRuntimeBuiltinToolCall


class RustProvidedToolCallAction(RustProtocolModel):
    type: Literal["provided_tool_call"] = "provided_tool_call"
    action_id: str
    turn_id: str
    call_id: str
    call: RustProvidedToolCall


class RustFilesystemWriteOperation(RustProtocolModel):
    type: Literal["write"] = "write"
    workspace_path: str = Field(min_length=1)
    content: str


RustFilesystemOperation = RustFilesystemWriteOperation


class RustFilesystemAction(RustProtocolModel):
    type: Literal["filesystem"] = "filesystem"
    action_id: str
    turn_id: str
    operation: RustFilesystemOperation


RustToolCallAction = Annotated[
    RustRuntimeBuiltinToolCallAction | RustProvidedToolCallAction,
    Field(discriminator="type"),
]


class RustHookToolCall(RustProtocolModel):
    action_id: str
    call_id: str
    call: RustExternalToolCall


class RustPreAgentTurnHookInput(RustProtocolModel):
    user_content: list[RustContentBlock] = Field(min_length=1)


class RustCompletionHookInput(RustProtocolModel):
    candidate: RustCompletionCandidate


class RustPreToolCallHookInput(RustProtocolModel):
    tool_call: RustHookToolCall


class RustPostToolCallHookInput(RustProtocolModel):
    tool_call: RustHookToolCall
    tool_result: RustToolResult


class RustHookCallActionBase(RustProtocolModel):
    type: Literal["hook_call"] = "hook_call"
    action_id: str
    turn_id: str
    hook_binding_ids: list[str] = Field(min_length=1)


class RustPreAgentTurnHookAction(RustHookCallActionBase):
    hook: Literal["pre_agent_turn"] = "pre_agent_turn"
    input: RustPreAgentTurnHookInput


class RustPreLlmCallHookAction(RustHookCallActionBase):
    hook: Literal["pre_llm_call"] = "pre_llm_call"


class RustPostLlmCallHookAction(RustHookCallActionBase):
    hook: Literal["post_llm_call"] = "post_llm_call"
    input: RustCompletionHookInput


class RustPostAgentTurnHookAction(RustHookCallActionBase):
    hook: Literal["post_agent_turn"] = "post_agent_turn"
    input: RustCompletionHookInput


class RustPreToolCallHookAction(RustHookCallActionBase):
    hook: Literal["pre_tool_call"] = "pre_tool_call"
    input: RustPreToolCallHookInput


class RustPostToolCallHookAction(RustHookCallActionBase):
    hook: Literal["post_tool_call"] = "post_tool_call"
    input: RustPostToolCallHookInput


RustHookCallAction = Annotated[
    RustPreAgentTurnHookAction
    | RustPreLlmCallHookAction
    | RustPostLlmCallHookAction
    | RustPostAgentTurnHookAction
    | RustPreToolCallHookAction
    | RustPostToolCallHookAction,
    Field(discriminator="hook"),
]

RustAction = (
    RustLLMCallAction
    | RustRuntimeBuiltinToolCallAction
    | RustProvidedToolCallAction
    | RustHookCallAction
    | RustFilesystemAction
)


class RustToolResultCommittedObservation(RustProtocolModel):
    type: Literal["tool_result_committed"] = "tool_result_committed"
    turn_id: str
    action_id: str
    call_id: str
    result: RustToolResult


class RustToolExecutionStartedObservation(RustProtocolModel):
    type: Literal["tool_execution_started"] = "tool_execution_started"
    turn_id: str
    call_id: str


class RustToolExecutionFinishedObservation(RustProtocolModel):
    type: Literal["tool_execution_finished"] = "tool_execution_finished"
    turn_id: str
    call_id: str
    result: RustToolResult


class RustBackgroundProcessNotificationSource(RustProtocolModel):
    type: Literal["background_process"] = "background_process"
    process_id: str = Field(min_length=1)
    status: Literal["completed", "failed", "stopped", "orphaned"]
    exit_code: int | None = None


class RustSubagentNotificationSource(RustProtocolModel):
    type: Literal["subagent"] = "subagent"
    agent_name: str = Field(min_length=1)
    status: Literal["completed", "failed", "interrupted"]


class RustAsyncToolNotificationSource(RustProtocolModel):
    type: Literal["async_tool"] = "async_tool"
    call_id: str = Field(min_length=1)
    status: Literal["completed", "failed"]


RustNotificationSource = Annotated[
    RustBackgroundProcessNotificationSource
    | RustSubagentNotificationSource
    | RustAsyncToolNotificationSource,
    Field(discriminator="type"),
]


class RustHarnessNotification(RustProtocolModel):
    id: str = Field(min_length=1)
    source: RustNotificationSource
    level: Literal["info", "warning", "error"]
    message: str = Field(min_length=1)
    content: list[RustContentBlock] = Field(default_factory=list)


class RustNotificationReceivedObservation(RustProtocolModel):
    type: Literal["notification_received"] = "notification_received"
    turn_id: str | None = None
    notification: RustHarnessNotification


class RustActionAbandonedObservation(RustProtocolModel):
    type: Literal["action_abandoned"] = "action_abandoned"
    turn_id: str
    action_id: str
    cause: Literal["steer", "interrupt", "runtime_failure"]


class RustTurnStartedObservation(RustProtocolModel):
    type: Literal["turn_started"] = "turn_started"
    turn_id: str
    content: list[RustContentBlock] = Field(min_length=1)


class RustTurnSteeringReceivedObservation(RustProtocolModel):
    type: Literal["turn_steering_received"] = "turn_steering_received"
    turn_id: str
    content: list[RustContentBlock] = Field(min_length=1)


class RustTurnSteeredObservation(RustProtocolModel):
    type: Literal["turn_steered"] = "turn_steered"
    turn_id: str
    content: list[RustContentBlock] = Field(min_length=1)


class RustNotificationDeliveredObservation(RustProtocolModel):
    type: Literal["notification_delivered"] = "notification_delivered"
    turn_id: str
    notification: RustHarnessNotification


class RustCompletionFailureDiscardCause(RustProtocolModel):
    type: Literal["failure"] = "failure"
    error: RustProtocolError


class RustCompletionSkippedDiscardCause(RustProtocolModel):
    type: Literal["skipped"] = "skipped"


class RustCompletionRetryDiscardCause(RustProtocolModel):
    type: Literal["retry"] = "retry"


class RustCompletionRejectedDiscardCause(RustProtocolModel):
    type: Literal["rejected"] = "rejected"


class RustCompletionSteerDiscardCause(RustProtocolModel):
    type: Literal["steer"] = "steer"


class RustCompletionInterruptDiscardCause(RustProtocolModel):
    type: Literal["interrupt"] = "interrupt"


RustCompletionDiscardCause = Annotated[
    RustCompletionFailureDiscardCause
    | RustCompletionSkippedDiscardCause
    | RustCompletionRetryDiscardCause
    | RustCompletionRejectedDiscardCause
    | RustCompletionSteerDiscardCause
    | RustCompletionInterruptDiscardCause,
    Field(discriminator="type"),
]


class RustAgentCompletionCandidateDiscardedObservation(RustProtocolModel):
    type: Literal["agent_completion_candidate_discarded"] = (
        "agent_completion_candidate_discarded"
    )
    turn_id: str
    action_id: str
    cause: RustCompletionDiscardCause


class RustAssistantMessageCommittedObservation(RustProtocolModel):
    type: Literal["assistant_message_committed"] = "assistant_message_committed"
    turn_id: str
    action_id: str
    candidate: RustCompletionCandidate


class RustToolDiscoverySummary(RustProtocolModel):
    kind: Literal["best_match", "details", "all_connector_capabilities"]
    tool_count: int = Field(ge=0)
    connector_notice_count: int = Field(ge=0)
    group_namespaces: list[str]


class RustToolDiscoveryFinishedObservation(RustProtocolModel):
    type: Literal["tool_discovery_finished"] = "tool_discovery_finished"
    turn_id: str
    call_id: str
    summary: RustToolDiscoverySummary


class RustLargeOutputSerializedObservation(RustProtocolModel):
    type: Literal["large_output_serialized"] = "large_output_serialized"
    turn_id: str
    call_id: str
    tool_name: str
    serialized_char_count: int = Field(ge=0)


class RustContextCompactedObservation(RustProtocolModel):
    type: Literal["context_compacted"] = "context_compacted"
    turn_id: str | None
    action_id: str
    compaction_id: str
    attempt: int = Field(gt=0)
    trigger: Literal["automatic", "manual"]
    summary: str = Field(min_length=1)
    usage: RustTokenUsage | None


class RustContextCompactionFailedObservation(RustProtocolModel):
    type: Literal["context_compaction_failed"] = "context_compaction_failed"
    turn_id: str | None
    action_id: str
    compaction_id: str
    attempt: int = Field(gt=0)
    trigger: Literal["automatic", "manual"]
    error: RustProtocolError


class RustTurnFailedObservation(RustProtocolModel):
    type: Literal["turn_failed"] = "turn_failed"
    turn_id: str
    error: RustProtocolError


type RustTurnStopReason = Literal["iteration_limit"]


class RustTurnCompletedObservation(RustProtocolModel):
    type: Literal["turn_completed"] = "turn_completed"
    turn_id: str
    output: list[RustContentBlock]
    stop_reason: RustTurnStopReason | None = None


class RustTurnInterruptedObservation(RustProtocolModel):
    type: Literal["turn_interrupted"] = "turn_interrupted"
    turn_id: str
    reason: str | None = None


RustObservation = Annotated[
    RustTurnStartedObservation
    | RustTurnSteeringReceivedObservation
    | RustTurnSteeredObservation
    | RustNotificationReceivedObservation
    | RustNotificationDeliveredObservation
    | RustToolExecutionStartedObservation
    | RustToolExecutionFinishedObservation
    | RustToolResultCommittedObservation
    | RustToolDiscoveryFinishedObservation
    | RustLargeOutputSerializedObservation
    | RustActionAbandonedObservation
    | RustAgentCompletionCandidateDiscardedObservation
    | RustAssistantMessageCommittedObservation
    | RustContextCompactedObservation
    | RustContextCompactionFailedObservation
    | RustTurnCompletedObservation
    | RustTurnInterruptedObservation
    | RustTurnFailedObservation,
    Field(discriminator="type"),
]


class RustIdleTurn(RustProtocolModel):
    status: Literal["idle"] = "idle"


class RustCompactingTurn(RustProtocolModel):
    status: Literal["compacting"] = "compacting"
    trigger: Literal["automatic", "manual"]


class RustRunningTurn(RustProtocolModel):
    status: Literal["running"] = "running"
    turn_id: str


class RustCompletedTurn(RustProtocolModel):
    status: Literal["completed"] = "completed"
    turn_id: str
    output: list[RustContentBlock]


class RustInterruptedTurn(RustProtocolModel):
    status: Literal["interrupted"] = "interrupted"
    turn_id: str
    reason: str | None = None


class RustFailedTurn(RustProtocolModel):
    status: Literal["failed"] = "failed"
    turn_id: str
    error: RustProtocolError


RustTurn = Annotated[
    RustIdleTurn
    | RustCompactingTurn
    | RustRunningTurn
    | RustCompletedTurn
    | RustInterruptedTurn
    | RustFailedTurn,
    Field(discriminator="status"),
]


class RustNoNextAction(RustProtocolModel):
    type: Literal["none"] = "none"


class RustKeepActionDirective(RustProtocolModel):
    type: Literal["keep"] = "keep"
    action_id: str


class RustDispatchActionDirective(RustProtocolModel):
    type: Literal["dispatch"] = "dispatch"
    action: RustAction


class RustRefreshActionDirective(RustProtocolModel):
    type: Literal["refresh"] = "refresh"
    action: RustLLMCallAction


RustActionDirective = Annotated[
    RustKeepActionDirective | RustDispatchActionDirective | RustRefreshActionDirective,
    Field(discriminator="type"),
]


class RustActionsNextAction(RustProtocolModel):
    type: Literal["actions"] = "actions"
    directives: list[RustActionDirective] = Field(min_length=1)


RustNextAction = Annotated[
    RustNoNextAction | RustActionsNextAction, Field(discriminator="type")
]


class RustTransition(RustProtocolModel):
    next: RustNextAction
    observations: list[RustObservation] = Field(default_factory=list)
    turn: RustTurn

    @model_validator(mode="after")
    def validate_next_for_turn(self) -> Self:
        if isinstance(self.turn, RustCompactingTurn):
            if isinstance(self.next, RustNoNextAction):
                raise ValueError("compacting transitions require a pending next action")
            if any(
                not isinstance(action, RustLLMCallAction)
                or action.purpose != "compaction"
                or action.turn_id is not None
                for action in self.actions
            ):
                raise ValueError("between-turn compaction requires compaction actions")
            return self
        if isinstance(self.turn, RustRunningTurn):
            if isinstance(self.next, RustNoNextAction):
                raise ValueError("running transitions require a pending next action")
            for action in self.actions:
                if action.turn_id != self.turn.turn_id:
                    raise ValueError(
                        "dispatched action turn_id must match the running turn"
                    )
            return self
        if not isinstance(self.next, RustNoNextAction):
            raise ValueError("terminal transitions require next none")
        return self

    @property
    def actions(self) -> list[RustAction]:
        if not isinstance(self.next, RustActionsNextAction):
            return []
        return [
            directive.action
            for directive in self.next.directives
            if isinstance(
                directive, RustDispatchActionDirective | RustRefreshActionDirective
            )
        ]


class RustUserMessageEvent(RustProtocolModel):
    type: Literal["user_message"] = "user_message"
    turn_id: str = Field(min_length=1)
    content: list[RustContentBlock] = Field(min_length=1)
    mode: Literal["queue", "steer"]


class RustContextMessageEvent(RustProtocolModel):
    type: Literal["context_message"] = "context_message"
    content: list[RustContentBlock] = Field(min_length=1)


class RustCompactEvent(RustProtocolModel):
    type: Literal["compact"] = "compact"
    extra_instructions: str = ""


class RustSystemInstructionsChange(RustProtocolModel):
    type: Literal["system_instructions"] = "system_instructions"
    value: str


class RustSettingsChange(RustProtocolModel):
    type: Literal["settings"] = "settings"
    value: RustHarnessSettings


class RustCapabilitiesChange(RustProtocolModel):
    type: Literal["capabilities"] = "capabilities"
    value: RustHarnessCapabilitySet


class RustPluginsChange(RustProtocolModel):
    type: Literal["plugins"] = "plugins"
    value: list[RustPluginContextDefinition]


RustHarnessConfigurationChange = Annotated[
    RustSystemInstructionsChange
    | RustSettingsChange
    | RustCapabilitiesChange
    | RustPluginsChange,
    Field(discriminator="type"),
]


class RustReconfigureEvent(RustProtocolModel):
    type: Literal["reconfigure"] = "reconfigure"
    changes: list[RustHarnessConfigurationChange] = Field(min_length=1)


class RustNotificationEvent(RustProtocolModel):
    type: Literal["notification"] = "notification"
    notification: RustHarnessNotification


class RustCompletionModelInputResyncRequestedEvent(RustProtocolModel):
    type: Literal["completion_model_input_resync_requested"] = (
        "completion_model_input_resync_requested"
    )
    action_id: str


class RustCompletionSucceededEvent(RustProtocolModel):
    type: Literal["completion_succeeded"] = "completion_succeeded"
    action_id: str
    result: RustCompletionResult


class RustCompletionFailedEvent(RustProtocolModel):
    type: Literal["completion_failed"] = "completion_failed"
    action_id: str
    error: RustProtocolError


class RustPreAgentTurnContinue(RustProtocolModel):
    type: Literal["continue"] = "continue"
    user_content: list[RustContentBlock] = Field(min_length=1)


class RustHookSkip(RustProtocolModel):
    type: Literal["skip"] = "skip"
    reason: list[RustContentBlock] = Field(default_factory=list)


class RustHookContinue(RustProtocolModel):
    type: Literal["continue"] = "continue"


class RustCompletionHookAccept(RustProtocolModel):
    type: Literal["accept"] = "accept"
    acceptance: RustAgentCompletionAcceptance


class RustCompletionHookRetry(RustProtocolModel):
    type: Literal["retry"] = "retry"
    feedback: list[RustContentBlock] = Field(min_length=1)


class RustCompletionHookReject(RustProtocolModel):
    type: Literal["reject"] = "reject"
    reason: list[RustContentBlock] = Field(min_length=1)


RustCompletionHookOutput = Annotated[
    RustCompletionHookAccept | RustCompletionHookRetry | RustCompletionHookReject,
    Field(discriminator="type"),
]


class RustPreToolCallContinue(RustProtocolModel):
    type: Literal["continue"] = "continue"
    effective_arguments: JsonObject = Field(default_factory=dict)


class RustPreAgentTurnHookResult(RustProtocolModel):
    hook: Literal["pre_agent_turn"] = "pre_agent_turn"
    output: Annotated[
        RustPreAgentTurnContinue | RustHookSkip, Field(discriminator="type")
    ]


class RustPreLlmCallHookResult(RustProtocolModel):
    hook: Literal["pre_llm_call"] = "pre_llm_call"
    output: Annotated[RustHookContinue | RustHookSkip, Field(discriminator="type")]


class RustPostLlmCallHookResult(RustProtocolModel):
    hook: Literal["post_llm_call"] = "post_llm_call"
    output: RustCompletionHookOutput


class RustPostAgentTurnHookResult(RustProtocolModel):
    hook: Literal["post_agent_turn"] = "post_agent_turn"
    output: RustCompletionHookOutput


class RustPreToolCallHookResult(RustProtocolModel):
    hook: Literal["pre_tool_call"] = "pre_tool_call"
    output: Annotated[
        RustPreToolCallContinue | RustHookSkip, Field(discriminator="type")
    ]


class RustPostToolCallOutput(RustProtocolModel):
    tool_result: RustToolResult


class RustPostToolCallHookResult(RustProtocolModel):
    hook: Literal["post_tool_call"] = "post_tool_call"
    output: RustPostToolCallOutput


RustHookResult = Annotated[
    RustPreAgentTurnHookResult
    | RustPreLlmCallHookResult
    | RustPostLlmCallHookResult
    | RustPostAgentTurnHookResult
    | RustPreToolCallHookResult
    | RustPostToolCallHookResult,
    Field(discriminator="hook"),
]


class RustHookCompletedEvent(RustProtocolModel):
    type: Literal["hook_completed"] = "hook_completed"
    action_id: str
    result: RustHookResult


class RustHookFailedEvent(RustProtocolModel):
    type: Literal["hook_failed"] = "hook_failed"
    action_id: str
    error: RustProtocolError


class RustToolResultEvent(RustProtocolModel):
    type: Literal["tool_result"] = "tool_result"
    action_id: str
    output: JsonValue = None
    content: list[RustContentBlock] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    error: str | None = None
    result: RustToolResult | None = None
    effective_arguments: JsonObject | None = None


class RustToolSucceededEvent(RustProtocolModel):
    type: Literal["tool_succeeded"] = "tool_succeeded"
    action_id: str
    call_id: str
    result: RustToolSuccessResult


class RustToolFailedEvent(RustProtocolModel):
    type: Literal["tool_failed"] = "tool_failed"
    action_id: str
    call_id: str
    result: RustToolFailureResult


class RustFilesystemWriteResult(RustProtocolModel):
    type: Literal["write"] = "write"
    model_path: str = Field(min_length=1)


RustFilesystemResult = RustFilesystemWriteResult


class RustFilesystemSucceededEvent(RustProtocolModel):
    type: Literal["filesystem_succeeded"] = "filesystem_succeeded"
    action_id: str
    result: RustFilesystemResult


class RustFilesystemFailedEvent(RustProtocolModel):
    type: Literal["filesystem_failed"] = "filesystem_failed"
    action_id: str
    error: RustProtocolError


class RustFailTurnEvent(RustProtocolModel):
    type: Literal["fail_turn"] = "fail_turn"
    expected_turn_id: str = Field(min_length=1)
    action_id: str
    error: RustProtocolError


class RustInterruptEvent(RustProtocolModel):
    type: Literal["interrupt"] = "interrupt"
    expected_turn_id: str = Field(min_length=1)
    reason: str | None = None


RustEvent = Annotated[
    RustUserMessageEvent
    | RustContextMessageEvent
    | RustCompactEvent
    | RustReconfigureEvent
    | RustNotificationEvent
    | RustCompletionModelInputResyncRequestedEvent
    | RustCompletionSucceededEvent
    | RustCompletionFailedEvent
    | RustHookCompletedEvent
    | RustHookFailedEvent
    | RustToolSucceededEvent
    | RustToolFailedEvent
    | RustFilesystemSucceededEvent
    | RustFilesystemFailedEvent
    | RustFailTurnEvent
    | RustInterruptEvent,
    Field(discriminator="type"),
]


class RustPendingCompletionAction(RustProtocolModel):
    type: Literal["completion"] = "completion"
    purpose: Literal["agent", "compaction"] = "agent"
    action_id: str


class RustPendingRuntimeBuiltinToolCallAction(RustProtocolModel):
    type: Literal["runtime_builtin_tool_call"] = "runtime_builtin_tool_call"
    action_id: str
    call_id: str
    name: RustRuntimeBuiltinToolName


class RustPendingProvidedToolCallAction(RustProtocolModel):
    type: Literal["provided_tool_call"] = "provided_tool_call"
    action_id: str
    call_id: str
    group_name: str
    tool_name: str


class RustPendingHookCallAction(RustProtocolModel):
    type: Literal["hook_call"] = "hook_call"
    action_id: str
    hook: RustHookPoint
    hook_binding_ids: list[str] = Field(min_length=1)


class RustPendingFilesystemWriteOperation(RustProtocolModel):
    type: Literal["write"] = "write"
    workspace_path: str = Field(min_length=1)


RustPendingFilesystemOperation = RustPendingFilesystemWriteOperation


class RustPendingFilesystemAction(RustProtocolModel):
    type: Literal["filesystem"] = "filesystem"
    action_id: str
    operation: RustPendingFilesystemOperation


RustPendingAction = Annotated[
    RustPendingCompletionAction
    | RustPendingRuntimeBuiltinToolCallAction
    | RustPendingProvidedToolCallAction
    | RustPendingHookCallAction
    | RustPendingFilesystemAction,
    Field(discriminator="type"),
]


class RustSessionInspection(RustProtocolModel):
    protocol_version: Literal[1]
    status: Literal["idle", "running", "compacting", "completed", "failed"]
    active_turn_id: str | None
    last_turn_id: str | None
    pending_actions: list[RustPendingAction] = Field(default_factory=list)
    message_count: int = Field(ge=0)
    context_revision: int = Field(ge=0)
    tool_catalog_revision: int = Field(ge=0)
    last_input_id: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.status == "compacting":
            if self.active_turn_id is not None or len(self.pending_actions) != 1:
                raise ValueError(
                    "compacting inspection requires one pending action and no active turn"
                )
            pending = self.pending_actions[0]
            if (
                not isinstance(pending, RustPendingCompletionAction)
                or pending.purpose != "compaction"
            ):
                raise ValueError("compacting inspection requires a compaction action")
            return self
        if self.status == "running":
            if self.active_turn_id is None or not self.pending_actions:
                raise ValueError(
                    "running inspection requires active_turn_id and pending_actions"
                )
            return self
        if self.active_turn_id is not None or self.pending_actions:
            raise ValueError(
                "non-running inspection cannot expose an active turn or pending actions"
            )
        return self


class RustDeterminismContext(RustProtocolModel):
    time_unix_ms: int = Field(ge=0, le=9_007_199_254_740_991)
    random_seed: int = Field(ge=0, le=4_294_967_295)


class RustHarnessInput(RustProtocolModel):
    protocol_version: Literal[1] = 1
    input_id: int = Field(ge=1)
    determinism: RustDeterminismContext
    command: RustEvent


class RustSessionTransition(RustTransition):
    protocol_version: Literal[1]
    input_id: int = Field(ge=1)


class RustStaleInputRejection(RustProtocolModel):
    code: Literal["stale_input"] = "stale_input"
    received_input_id: int = Field(ge=1)
    last_accepted_input_id: int = Field(ge=0)


class RustOutOfOrderInputRejection(RustProtocolModel):
    code: Literal["out_of_order_input"] = "out_of_order_input"
    received_input_id: int = Field(ge=1)
    expected_input_id: int = Field(ge=1)


class RustInputConflictRejection(RustProtocolModel):
    code: Literal["input_conflict"] = "input_conflict"
    input_id: int = Field(ge=1)


class RustInvalidStateRejection(RustProtocolModel):
    code: Literal["invalid_state"] = "invalid_state"
    command_type: str
    state: Literal["idle", "running", "compacting"]


class RustInvalidCorrelationRejection(RustProtocolModel):
    code: Literal["invalid_correlation"] = "invalid_correlation"
    received_action_id: str
    pending_action_ids: list[str] = Field(default_factory=list)


class RustInvalidCommandRejection(RustProtocolModel):
    code: Literal["invalid_command"] = "invalid_command"
    error: RustProtocolError


RustCommandRejection = Annotated[
    RustStaleInputRejection
    | RustOutOfOrderInputRejection
    | RustInputConflictRejection
    | RustInvalidStateRejection
    | RustInvalidCorrelationRejection
    | RustInvalidCommandRejection,
    Field(discriminator="code"),
]


class RustAcceptedApplyResult(RustProtocolModel):
    type: Literal["accepted"] = "accepted"
    transition: RustSessionTransition


class RustRejectedApplyResult(RustProtocolModel):
    type: Literal["rejected"] = "rejected"
    rejection: RustCommandRejection


RustApplyResult = Annotated[
    RustAcceptedApplyResult | RustRejectedApplyResult, Field(discriminator="type")
]

_RUST_APPLY_RESULT_ADAPTER: TypeAdapter[RustApplyResult] = TypeAdapter(RustApplyResult)


def parse_apply_result(value: str) -> RustApplyResult:
    return _RUST_APPLY_RESULT_ADAPTER.validate_json(value)


def _is_programmatic_identifier(value: str) -> bool:
    if not value:
        return False
    first, *rest = value
    return (first in "_$" or first.isascii() and first.isalpha()) and all(
        character in "_$" or character.isascii() and character.isalnum()
        for character in rest
    )


def _is_skill_name(value: str) -> bool:
    namespace, separator, skill_name = value.partition(":")
    if not separator:
        return _is_unqualified_skill_name(value)
    return (
        ":" not in skill_name
        and _is_programmatic_identifier(namespace)
        and _is_unqualified_skill_name(skill_name)
    )


def _is_unqualified_skill_name(value: str) -> bool:
    return bool(value) and all(
        segment
        and all(character.isascii() and character.isalnum() for character in segment)
        for segment in value.split("-")
    )


def _reject_duplicates(description: str, values: list[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {description} {value!r}")
        seen.add(value)


def json_schema_object(schema: JsonSchema) -> JsonObject:
    if isinstance(schema, dict):
        return schema
    return {} if schema else {"not": {}}


__all__ = [
    "RUNTIME_BUILTIN_TOOL_NAMES",
    "JsonObject",
    "JsonSchema",
    "RustAcceptCandidate",
    "RustAcceptedApplyResult",
    "RustAction",
    "RustActionAbandonedObservation",
    "RustActionDirective",
    "RustActionsNextAction",
    "RustAgentCompletionAcceptance",
    "RustAgentCompletionCandidateDiscardedObservation",
    "RustAgentTypeDefinition",
    "RustAlwaysHookSelector",
    "RustApplyResult",
    "RustAssistantMessage",
    "RustAssistantMessageCommittedObservation",
    "RustAssistantPart",
    "RustAsyncToolNotificationSource",
    "RustAudioContentBlock",
    "RustAutomaticCompactionPolicy",
    "RustBackgroundProcessNotificationSource",
    "RustBlobResourceContents",
    "RustCandidateMessage",
    "RustCapabilitiesChange",
    "RustCommandEnvironment",
    "RustCommandRejection",
    "RustCompactEvent",
    "RustCompactEvent",
    "RustCompactingTurn",
    "RustCompactingTurn",
    "RustCompactionPolicy",
    "RustCompletedTurn",
    "RustCompletionCandidate",
    "RustCompletionFailedEvent",
    "RustCompletionFinishReason",
    "RustCompletionHookAccept",
    "RustCompletionHookReject",
    "RustCompletionHookRetry",
    "RustCompletionModelInputResyncRequestedEvent",
    "RustCompletionResult",
    "RustCompletionResultPart",
    "RustCompletionResultToolCallPart",
    "RustCompletionSucceededEvent",
    "RustConnectorToolGroupMetadata",
    "RustContentAnnotations",
    "RustContentBlock",
    "RustContentBlockMetadata",
    "RustContentIcon",
    "RustContextCompactedObservation",
    "RustContextCompactionFailedObservation",
    "RustContextMessageEvent",
    "RustContextSettings",
    "RustDeterminismContext",
    "RustDisabledCommandEnvironment",
    "RustDisabledCompactionPolicy",
    "RustDisabledLargeOutputPolicy",
    "RustDisabledRuntimeToolFeature",
    "RustDispatchActionDirective",
    "RustEmbeddedResourceContentBlock",
    "RustEnabledRuntimeToolFeature",
    "RustEvent",
    "RustExternalToolCall",
    "RustFailTurnEvent",
    "RustFailedTurn",
    "RustFilesystemAction",
    "RustFilesystemFailedEvent",
    "RustFilesystemLargeOutputPolicy",
    "RustFilesystemOperation",
    "RustFilesystemResult",
    "RustFilesystemSucceededEvent",
    "RustFilesystemWriteOperation",
    "RustFilesystemWriteResult",
    "RustGitBashCommandEnvironment",
    "RustHarnessCapabilitySet",
    "RustHarnessConfig",
    "RustHarnessConfigUpdate",
    "RustHarnessConfigurationChange",
    "RustHarnessHookBinding",
    "RustHarnessHookSelector",
    "RustHarnessHookToolKey",
    "RustHarnessInput",
    "RustHarnessNotification",
    "RustHarnessSettings",
    "RustHookCallAction",
    "RustHookCompletedEvent",
    "RustHookContinue",
    "RustHookFailedEvent",
    "RustHookPoint",
    "RustHookResult",
    "RustHookSkip",
    "RustHookToolCall",
    "RustIdleTurn",
    "RustImageContentBlock",
    "RustImageDeliverySettings",
    "RustInMemoryBashCommandEnvironment",
    "RustInputConflictRejection",
    "RustInterruptEvent",
    "RustInterruptedTurn",
    "RustInvalidCommandRejection",
    "RustInvalidCorrelationRejection",
    "RustInvalidJsonToolArguments",
    "RustInvalidStateRejection",
    "RustJsonToolArguments",
    "RustKeepActionDirective",
    "RustKnowledgeFolderDefinition",
    "RustLLMCallAction",
    "RustLargeOutputPolicy",
    "RustLargeOutputSerializedObservation",
    "RustMessage",
    "RustModelInputUpdate",
    "RustModelMessageAppend",
    "RustModelMessageReplace",
    "RustModelMessageUpdate",
    "RustModelToolCallPart",
    "RustModelToolCatalogKeep",
    "RustModelToolCatalogReplace",
    "RustModelToolCatalogUpdate",
    "RustNextAction",
    "RustNoNextAction",
    "RustNotificationDeliveredObservation",
    "RustNotificationEvent",
    "RustNotificationReceivedObservation",
    "RustNotificationSource",
    "RustObservation",
    "RustOutOfOrderInputRejection",
    "RustPendingAction",
    "RustPendingCompletionAction",
    "RustPendingFilesystemAction",
    "RustPendingFilesystemOperation",
    "RustPendingFilesystemWriteOperation",
    "RustPendingHookCallAction",
    "RustPendingProvidedToolCallAction",
    "RustPendingRuntimeBuiltinToolCallAction",
    "RustPluginContextDefinition",
    "RustPluginsChange",
    "RustPostAgentTurnHookAction",
    "RustPostAgentTurnHookResult",
    "RustPostLlmCallHookAction",
    "RustPostLlmCallHookResult",
    "RustPostToolCallHookAction",
    "RustPostToolCallHookResult",
    "RustPostToolCallOutput",
    "RustPowerShellCommandEnvironment",
    "RustPreAgentTurnContinue",
    "RustPreAgentTurnHookAction",
    "RustPreAgentTurnHookResult",
    "RustPreLlmCallHookAction",
    "RustPreLlmCallHookResult",
    "RustPreToolCallContinue",
    "RustPreToolCallHookAction",
    "RustPreToolCallHookResult",
    "RustProgrammaticToolSettings",
    "RustProtocolError",
    "RustProtocolModel",
    "RustProvidedToolCall",
    "RustProvidedToolCallAction",
    "RustProvidedToolDefinition",
    "RustReasoningContent",
    "RustReasoningPart",
    "RustReasoningRedactedContent",
    "RustReasoningSummaryContent",
    "RustReasoningTextContent",
    "RustReconfigureEvent",
    "RustRefreshActionDirective",
    "RustRejectedApplyResult",
    "RustReplaceAssistantContent",
    "RustResourceContents",
    "RustResourceLinkContentBlock",
    "RustRunningTurn",
    "RustRuntimeBuiltinToolCall",
    "RustRuntimeBuiltinToolCallAction",
    "RustRuntimeBuiltinToolName",
    "RustRuntimeToolFeature",
    "RustSessionInspection",
    "RustSessionTransition",
    "RustSettingsChange",
    "RustSkillDefinition",
    "RustStaleInputRejection",
    "RustSubagentNotificationSource",
    "RustSystemInstructionsChange",
    "RustSystemMessage",
    "RustTextContentBlock",
    "RustTextResourceContents",
    "RustTokenUsage",
    "RustToolArguments",
    "RustToolCall",
    "RustToolCallAction",
    "RustToolDefinition",
    "RustToolDiscoveryFinishedObservation",
    "RustToolDiscoverySummary",
    "RustToolExecutionFinishedObservation",
    "RustToolExecutionStartedObservation",
    "RustToolFailedEvent",
    "RustToolFailureResult",
    "RustToolGroupDefinition",
    "RustToolKeysHookSelector",
    "RustToolMessage",
    "RustToolResult",
    "RustToolResultCommittedObservation",
    "RustToolResultEvent",
    "RustToolSettings",
    "RustToolSucceededEvent",
    "RustToolSuccessResult",
    "RustTransition",
    "RustTurn",
    "RustTurnCompletedObservation",
    "RustTurnFailedObservation",
    "RustTurnInterruptedObservation",
    "RustTurnSettings",
    "RustTurnStartedObservation",
    "RustTurnSteeredObservation",
    "RustTurnSteeringReceivedObservation",
    "RustTurnStopReason",
    "RustUnixCommandEnvironment",
    "RustUserMessage",
    "RustUserMessageEvent",
    "json_schema_object",
    "parse_apply_result",
    "tool_call_wire_arguments",
]
