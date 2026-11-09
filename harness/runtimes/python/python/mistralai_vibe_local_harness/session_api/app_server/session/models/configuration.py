"""Transport-neutral agent configuration accepted by Session procedures."""

import re
from collections.abc import Sequence
from hashlib import sha256
from pathlib import PurePath, PureWindowsPath
from typing import Annotated, Final, Literal, Self, cast
from urllib.parse import quote
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    JsonValue,
    SerializationInfo,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import PydanticCustomError

from .base import JsonSchema, SessionModel
from .common import AbsolutePath

type HookType = Literal[
    "pre_agent_turn",
    "post_agent_turn",
    "pre_llm_call",
    "post_llm_call",
    "pre_mcp_tool_call",
    "post_mcp_tool_call",
    "pre_client_tool_call",
    "post_client_tool_call",
    "pre_tool_call",
    "post_tool_call",
]
DEFAULT_CONCURRENT_CHILD_TURN_LIMIT: Final = 10
DEFAULT_MAX_SUBAGENT_DEPTH: Final = 2
type ToolModelAccess = Literal["direct", "programmatic", "both"]
"""How the model calls a tool: ``"programmatic"`` from ``run_typescript``, ``"direct"`` from its
tool list, or ``"both"``."""
type SandboxAccess = Literal["none", "files", "full"]
SANDBOX_ACCESS_RANK: Final[dict[SandboxAccess, int]] = {"none": 0, "files": 1, "full": 2}

_ENVIRONMENT_VARIABLE_NAME: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# An HTTP field name is an RFC 9110 token.
_HTTP_FIELD_NAME: Final = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
# Credentials whose alphabet is also a valid variable name, so the name check alone passes them.
_CREDENTIAL_PREFIXES: Final = (
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "ghr_",
    "github_pat_",
    "xoxb",
    "xoxp",
    "xoxa",
    "xoxe",
)
type PermissionDecision = Literal["allow", "deny", "ask"]
_REMOVED_TOOL_SELECTION_FIELDS: Final = (
    "enabled_tools",
    "enabledTools",
    "disabled_tools",
    "disabledTools",
    "flags",
)


class ProgrammaticToolCallingConfig(SessionModel):
    """Options of an enabled programmatic tool calling feature."""

    sleep: bool = Field(
        default=True,
        description="Expose `tools.self.sleep` to programs.",
    )


class BuiltinsConfig(SessionModel):
    """Harness-owned features the model can use.

    A disabled feature has no tool and no guidance in model context.
    """

    skills: bool = Field(
        default=True,
        description="Expose the `skill` tool while at least one skill is configured.",
    )
    subagents: bool = Field(
        default=True,
        description=(
            "Expose `tools.agents`. Requires programmatic tool calling. When off, a child "
            "keeps `send_message` and `wait` to reach the rest of its tree."
        ),
    )
    programmatic_tool_calling: bool | ProgrammaticToolCallingConfig = Field(
        default=True,
        description=(
            "Expose `run_typescript`, `search_tool_functions`, and the `tools.<group>` "
            "catalog. An options object enables the feature."
        ),
    )
    background_processes: bool | None = Field(
        default=None,
        description=(
            "Expose `tools.process`. Omitted enables them when the Host supports them, "
            "sandbox access is `full`, and programmatic tool calling is enabled. True fails "
            "Session start when the Host cannot honor it."
        ),
    )

    @property
    def programmatic_enabled(self) -> bool:
        return self.programmatic_tool_calling is not False

    @property
    def programmatic_sleep(self) -> bool:
        options = self.programmatic_tool_calling
        if isinstance(options, ProgrammaticToolCallingConfig):
            return options.sleep
        return options

    @model_validator(mode="after")
    def validate_programmatic_dependents(self) -> Self:
        if self.programmatic_enabled:
            return self
        if self.subagents:
            raise ValueError(
                "builtins.subagents requires builtins.programmatic_tool_calling; "
                "set subagents to false"
            )
        if self.background_processes:
            raise ValueError(
                "builtins.background_processes requires builtins.programmatic_tool_calling"
            )
        return self


class SubagentBuiltinsConfig(SessionModel):
    """One partial built-ins layer: omitted fields inherit and present fields replace.

    Explicit null is accepted only for `background_processes`, where it restores the Host
    default. Serialization preserves field presence.
    """

    skills: bool | None = None
    subagents: bool | None = None
    programmatic_tool_calling: bool | ProgrammaticToolCallingConfig | None = None
    background_processes: bool | None = None

    @model_validator(mode="after")
    def validate_layer(self) -> Self:
        for name in self.model_fields_set - {"background_processes"}:
            if getattr(self, name) is None:
                raise ValueError(f"builtins field {name!r} cannot be null; omit it to inherit")
        return self

    @model_serializer(mode="wrap")
    def serialize_layer(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, JsonValue]:
        return _serialize_present_fields(self, handler, info)

    def apply(self, parent: BuiltinsConfig) -> BuiltinsConfig:
        """Return ``parent`` with the fields this layer sets replaced."""

        values = {name: getattr(parent, name) for name in BuiltinsConfig.model_fields}
        values.update({name: getattr(self, name) for name in self.model_fields_set})
        return BuiltinsConfig.model_validate(values)


def reject_removed_tool_selection(data: object, *, owner: str) -> object:
    """Fail on a non-empty legacy tool selection instead of silently widening the agent."""

    if not isinstance(data, dict):
        return data
    for name in _REMOVED_TOOL_SELECTION_FIELDS:
        value = data.get(name)
        if value is not None and value != []:
            raise ValueError(
                f"{owner}.{name} was removed; use builtins and sandbox_access to turn off "
                "Harness features, and the MCP or connector configuration to filter their tools"
            )
    return data


class PermissionRule(SessionModel):
    """Permission behavior for calls whose stable tool ID matches ``tools``."""

    model_config = ConfigDict(frozen=True)

    tools: tuple[str, ...]
    """Glob patterns of the stable tool IDs, such as ``client.deploy_service``, that select the
    rule."""
    decision: PermissionDecision
    """``"allow"``, ``"ask"`` or ``"deny"``: the decision when no argument pattern decides."""
    allowlist: tuple[str, ...] = ()
    """Argument patterns that run without approval, unless ``denylist`` or ``sensitive`` matches;
    when not empty, other calls ask."""
    denylist: tuple[str, ...] = ()
    """Argument patterns that cannot run."""
    sensitive: tuple[str, ...] = ()
    """Argument patterns that ask for approval."""


class PermissionPolicy(SessionModel):
    """Ordered permission rules and the fallback for calls that match no rule."""

    model_config = ConfigDict(frozen=True)

    default: PermissionDecision
    """``"allow"``, ``"ask"`` or ``"deny"``: the decision for a call that matches no rule."""
    rules: tuple[PermissionRule, ...] = ()
    """The rules, in order: the first whose ``tools`` match the call applies."""


class CapabilityBindingReference(SessionModel):
    """Stable serializable identity matched to one live Host registration."""

    id: Annotated[str, Field(min_length=1)]


class ToolAnnotations(SessionModel):
    """Hints about what a tool does, as the Model Context Protocol (MCP) names them.

    A hint the tool does not declare is absent, and an approver reads it with MCP's default:
    not read-only, destructive, not idempotent, open world. They are for approvers; the model
    does not see them.
    """

    read_only_hint: bool | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The tool does not change its environment. MCP default: false.",
    )
    destructive_hint: bool | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The tool may delete or overwrite data. MCP default: true.",
    )
    idempotent_hint: bool | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="Repeat calls with the same arguments change nothing more. MCP default: false.",
    )
    open_world_hint: bool | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description="The tool reaches systems outside the agent's own. MCP default: true.",
    )


class ToolDefinition(SessionModel):
    """Caller-declared tool whose execution is supplied by a Host registration."""

    namespace: Annotated[str | None, Field(min_length=1)] = None
    name: Annotated[str, Field(min_length=1)]
    description: str = ""
    input_schema: JsonSchema = Field(default_factory=dict)
    output_schema: JsonSchema = Field(default_factory=dict)
    model_access: ToolModelAccess = "programmatic"
    required_session_input_ids: Annotated[list[str], Field(default_factory=list)] = Field(
        default_factory=list,
        description="Session input IDs this tool requires, never part of its model-visible schema.",
    )
    annotations: ToolAnnotations | None = Field(
        default=None,
        exclude_if=lambda value: value is None,
        description=(
            "Hints about what the tool does, sent to approvers with each approval of it. "
            "The model does not see them."
        ),
    )

    @property
    def binding_reference(self) -> CapabilityBindingReference:
        namespace = "" if self.namespace is None else quote(self.namespace, safe="")
        name = quote(self.name, safe="")
        return CapabilityBindingReference(id=f"tool:{namespace}:{name}")


class AlwaysHookMatcher(SessionModel):
    type: Literal["always"] = "always"


class ToolNameHookMatcher(SessionModel):
    type: Literal["tool_names"] = "tool_names"
    tool_names: Annotated[list[str], Field(min_length=1)]


type HookMatcher = Annotated[
    AlwaysHookMatcher | ToolNameHookMatcher,
    Field(discriminator="type"),
]


class HookDefinition(SessionModel):
    type: HookType
    name: Annotated[str, Field(min_length=1)]
    matcher: HookMatcher = Field(default_factory=AlwaysHookMatcher)
    required_session_input_ids: Annotated[list[str], Field(default_factory=list)] = Field(
        default_factory=list,
        description="Session input IDs this hook requires.",
    )

    @property
    def binding_reference(self) -> CapabilityBindingReference:
        return CapabilityBindingReference(id=f"hook:{quote(self.name, safe='')}")

    @field_validator("matcher", mode="before")
    @classmethod
    def normalize_matcher(cls, value: object) -> object:
        if value is None or value == {}:
            return {"type": "always"}
        if isinstance(value, dict) and "toolNames" in value and "tool_names" not in value:
            return {**value, "tool_names": value["toolNames"]}
        return value


class McpHttpSourceConfiguration(SessionModel):
    """HTTP MCP source whose credentials are supplied by the host."""

    # A value in `auth_header_env_vars` may be a credential pasted in place of a variable name, so
    # no validation error may print the input.
    model_config = ConfigDict(hide_input_in_errors=True)

    transport: Literal["http"] = "http"
    id: Annotated[str | None, Field(min_length=1)] = None
    name: Annotated[str | None, Field(min_length=1)] = None
    namespace: Annotated[str | None, Field(min_length=1)] = None
    url: Annotated[str, Field(min_length=1)]
    auth_header_env_vars: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Header name to the NAME of the environment variable holding that header's complete "
            "value, never the value itself. The host resolves each name when it makes the request, "
            "so no credential is carried in Session state. Each header name must be an HTTP token "
            "such as 'Authorization' or 'X-Api-Key', and appear once: names are case-insensitive."
        ),
    )
    model_access: ToolModelAccess = "programmatic"
    disabled_tools: list[str] = Field(default_factory=list)
    startup_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    tool_timeout_seconds: Annotated[float, Field(gt=0)] = 300.0

    @field_validator("auth_header_env_vars")
    @classmethod
    def validate_auth_header_env_vars(cls, value: dict[str, str]) -> dict[str, str]:
        # A rejected variable is never echoed: it may be a credential pasted in place of its name.
        seen: dict[str, str] = {}
        for header, variable in value.items():
            if not header.strip():
                raise ValueError(
                    "auth_header_env_vars maps a header name to an environment variable name; "
                    "a header name may not be blank"
                )
            if not _HTTP_FIELD_NAME.fullmatch(header):
                raise ValueError(
                    f"auth_header_env_vars has an invalid header name {header!r}: a header name "
                    "is an HTTP token such as 'Authorization' or 'X-Api-Key', without spaces, "
                    "colons or other separators"
                )
            if (previous := seen.setdefault(header.lower(), header)) != header:
                raise ValueError(
                    f"auth_header_env_vars sets the same header twice, as {previous!r} and "
                    f"{header!r}; header names are case-insensitive"
                )
            if not is_environment_variable_name(variable):
                raise ValueError(
                    f"auth_header_env_vars[{header!r}] must be the NAME of an environment variable "
                    "holding the header value, such as 'MY_MCP_TOKEN', never the value itself"
                )
        return value


def is_environment_variable_name(variable: str) -> bool:
    """Whether ``variable`` names an environment variable and does not look like a credential."""

    return bool(_ENVIRONMENT_VARIABLE_NAME.fullmatch(variable)) and not variable.startswith(
        _CREDENTIAL_PREFIXES
    )


class McpHostSourceConfiguration(SessionModel):
    """Host-resolved MCP source whose endpoint is never serialized in Runtime state."""

    transport: Literal["host"] = "host"
    id: Annotated[str | None, Field(min_length=1)] = None
    name: Annotated[str | None, Field(min_length=1)] = None
    namespace: Annotated[str | None, Field(min_length=1)] = None
    source_ref: Annotated[str | None, Field(min_length=1)] = None
    model_access: ToolModelAccess = "programmatic"
    disabled_tools: list[str] = Field(default_factory=list)
    startup_timeout_seconds: Annotated[float, Field(gt=0)] = 30.0
    tool_timeout_seconds: Annotated[float, Field(gt=0)] = 300.0


type McpSourceConfiguration = Annotated[
    McpHttpSourceConfiguration | McpHostSourceConfiguration,
    Field(discriminator="transport"),
]


class ConnectorAllSelection(SessionModel):
    type: Literal["all"] = "all"


class ConnectorAllowlistSelection(SessionModel):
    type: Literal["allowlist"] = "allowlist"
    connector_ids: Annotated[list[str], Field(min_length=1)]


type ConnectorSelection = Annotated[
    ConnectorAllSelection | ConnectorAllowlistSelection,
    Field(discriminator="type"),
]
type MaterializedSkillPath = Annotated[AbsolutePath, Field(pattern=r"(?:^|[\\/])SKILL\.md$")]


class MistralConnectorConfiguration(SessionModel):
    """Mistral connector discovery and authentication policy."""

    selection: ConnectorSelection = Field(default_factory=ConnectorAllSelection)
    credential_names: dict[str, str] = Field(default_factory=dict)
    authentication: Literal["interactive", "existing_only"] = "interactive"
    execution_identity: Literal["auto", "deployment"] = "auto"


class SandboxConfig(SessionModel):
    type: Literal["local", "managed"] = "local"
    image: str | None = None
    size: str | None = None
    network_access: bool | None = None
    env: dict[str, str] = Field(default_factory=dict)
    options: dict[str, JsonValue] = Field(default_factory=dict)


class SkillDefinition(SessionModel):
    """Legacy inline skill available to an app-server session."""

    name: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1)]
    content: Annotated[str, Field(min_length=1)]
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_materialized_path(cls, data: object) -> object:
        if isinstance(data, dict) and "path" in data:
            raise PydanticCustomError(
                "skill_inline_content_migration_required",
                "inline and materialized skill fields cannot be combined",
            )
        return data


class MaterializedSkillDefinition(SessionModel):
    """One materialized skill available to a Unified Harness remote session."""

    name: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=1)]
    path: MaterializedSkillPath

    @model_validator(mode="before")
    @classmethod
    def reject_inline_content(cls, data: object) -> object:
        if isinstance(data, dict) and "content" in data:
            raise PydanticCustomError(
                "skill_inline_content_migration_required",
                "inline skill content is unsupported; provide a materialized SKILL.md path",
            )
        return data

    @field_validator("description", "path")
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not (PurePath(value).is_absolute() or PureWindowsPath(value).is_absolute()):
            raise ValueError("skill path must be absolute")
        return value


type SessionSkillDefinition = SkillDefinition | MaterializedSkillDefinition


class LocalSkillSourceFile(SessionModel):
    path: Annotated[str, Field(min_length=1)]
    content_base64: str


class LocalSkillSourceConfiguration(SessionModel):
    type: Literal["local"] = "local"
    files: list[LocalSkillSourceFile] = Field(default_factory=list)


class RegistrySkillSourceConfiguration(SessionModel):
    type: Literal["registry"] = "registry"
    id: UUID
    version: Annotated[int | None, Field(ge=1)] = None


type SkillSourceConfiguration = Annotated[
    LocalSkillSourceConfiguration | RegistrySkillSourceConfiguration,
    Field(discriminator="type"),
]


class SkillInlineContentMigrationError(ValueError):
    pass


def calculate_skill_catalog_fingerprint(skills: Sequence[MaterializedSkillDefinition]) -> str:
    """Calculate the canonical fingerprint for one materialized skill catalog."""

    digest = sha256()
    for skill in sorted(skills, key=lambda value: value.name.encode("utf-8")):
        for value in (skill.name, skill.description, skill.path):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(4, byteorder="big"))
            digest.update(encoded)
    return digest.hexdigest()


class SubagentPolicy(SessionModel):
    """Delegation limits for one Session tree, read from the root configuration only."""

    concurrent_child_turn_limit: Annotated[int, Field(ge=1)] = DEFAULT_CONCURRENT_CHILD_TURN_LIMIT
    # The deepest level that may exist below the root (depth 0); agents at it cannot spawn.
    max_depth: Annotated[int, Field(ge=1)] = DEFAULT_MAX_SUBAGENT_DEPTH
    idle_message_starts: Literal["direct_children", "none"] = "direct_children"


class SubagentConfig(SessionModel):
    """One named partial layer; omitted fields inherit and present collections replace.

    Serialization preserves field presence, including explicit null for nullable
    settings, so Workflow inputs retain the author's inheritance choices.
    """

    description: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    completion: dict[str, JsonValue] | None = None
    max_iterations: Annotated[int | None, Field(gt=0)] = None
    first_output_timeout_seconds: Annotated[float | None, Field(gt=0)] = None
    builtins: SubagentBuiltinsConfig | None = None
    sandbox_access: SandboxAccess | None = Field(
        default=None,
        description=(
            "Replaces the parent's level. Explicit null grants everything the Host sandbox "
            "supports."
        ),
    )
    auto_compact_threshold: Annotated[int | None, Field(gt=0)] = Field(
        default=None, description="Replaces the parent's threshold; omitted inherits it."
    )
    tools: list[ToolDefinition] | None = None
    hooks: list[HookDefinition] | None = None
    mcps: dict[str, McpSourceConfiguration] | None = None
    connectors: MistralConnectorConfiguration | None = None
    skills: list[SessionSkillDefinition] | None = None
    skill_sources: list[SkillSourceConfiguration] | None = None
    subagents: "dict[str, SubagentConfig] | None" = None

    @model_validator(mode="before")
    @classmethod
    def reject_permission_policy(cls, data: object) -> object:
        """Every agent in a tree uses the root's permission policy."""
        if isinstance(data, dict) and ("permission_policy" in data or "permissionPolicy" in data):
            raise PydanticCustomError(
                "subagent_permission_policy_unsupported",
                "permission_policy can't be set on a subagent; set it on the root agent. "
                "Every subagent uses the root's policy.",
            )
        return data

    @field_validator("description", "instructions")
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("subagents")
    @classmethod
    def reject_blank_subagent_names(
        cls, value: "dict[str, SubagentConfig] | None"
    ) -> "dict[str, SubagentConfig] | None":
        return _reject_blank_subagent_names(value) if value is not None else None

    @model_validator(mode="before")
    @classmethod
    def reject_removed_fields(cls, data: object) -> object:
        return reject_removed_tool_selection(data, owner="SubagentConfig")

    @model_validator(mode="after")
    def validate_layer(self) -> Self:
        nullable = {
            "max_iterations",
            "first_output_timeout_seconds",
            "sandbox_access",
            "connectors",
        }
        for name in self.model_fields_set - nullable:
            if getattr(self, name) is None:
                raise ValueError(f"Subagent field {name!r} cannot be null; omit it to inherit")
        return self

    @model_serializer(mode="wrap")
    def serialize_layer(
        self, handler: SerializerFunctionWrapHandler, info: SerializationInfo
    ) -> dict[str, JsonValue]:
        return _serialize_present_fields(self, handler, info)


def _serialize_present_fields(
    layer: SessionModel, handler: SerializerFunctionWrapHandler, info: SerializationInfo
) -> dict[str, JsonValue]:
    """Serialize only the fields a partial layer sets, keeping an explicit null."""

    serialized = cast(dict[str, JsonValue], handler(layer))
    result: dict[str, JsonValue] = {}
    for name in layer.model_fields_set:
        if info.include is not None and name not in info.include:
            continue
        if info.exclude is not None and name in info.exclude:
            continue
        field = type(layer).model_fields[name]
        key = (field.serialization_alias or name) if info.by_alias is not False else name
        if key in serialized:
            result[key] = serialized[key]
        elif getattr(layer, name) is None:
            result[key] = None
    return result


def _reject_blank_subagent_names(
    value: dict[str, SubagentConfig],
) -> dict[str, SubagentConfig]:
    for name in value:
        if not name.strip():
            raise ValueError("Subagent names must not be blank")
    return value


class AgentConfig(SessionModel):
    """Serializable configuration shared by local and remote Session Runtimes."""

    completion: dict[str, JsonValue] = Field(
        default_factory=lambda: {"type": "mistral", "model": "mistral-small-latest"}
    )
    name: str = "sdk_agent"
    max_iterations: Annotated[int | None, Field(gt=0)] = None
    first_output_timeout_seconds: Annotated[float | None, Field(gt=0)] = None
    builtins: BuiltinsConfig = Field(default_factory=BuiltinsConfig)
    sandbox_access: SandboxAccess | None = Field(
        default=None,
        description=(
            "`none` exposes no sandbox tool, `files` exposes the file tools, and `full` adds "
            "`bash`. Omitted grants everything the Host sandbox supports; without a sandbox "
            "the model has neither. A level the Host cannot honor fails Session start."
        ),
    )
    sandbox: SandboxConfig | None = None
    instructions: str = ""
    auto_compact_threshold: Annotated[int | None, Field(gt=0)] = Field(
        default=None,
        description=(
            "Context tokens that start an automatic compaction. Compaction is always on; "
            "null uses the model's default, and a value outside the model's range fails "
            "Session start."
        ),
    )
    workdir: AbsolutePath | None = None
    tools: list[ToolDefinition] = Field(default_factory=list)
    skills: list[SessionSkillDefinition] = Field(default_factory=list)
    skill_catalog_fingerprint: Annotated[str | None, Field(pattern=r"^[0-9a-f]{64}$")] = None
    skill_sources: list[SkillSourceConfiguration] = Field(default_factory=list)
    mcps: dict[str, McpSourceConfiguration] = Field(default_factory=dict)
    connectors: MistralConnectorConfiguration | None = None
    guardrails: list[HookDefinition] = Field(
        default_factory=list,
        exclude_if=lambda value: not value,
    )
    hooks: list[HookDefinition] = Field(default_factory=list)
    subagent_policy: SubagentPolicy | None = None
    subagents: dict[str, SubagentConfig] = Field(default_factory=dict)
    permission_policy: PermissionPolicy | None = None

    def effective_subagent_policy(self) -> SubagentPolicy | None:
        """Return the declared tree policy, or the default when subagents are enabled."""
        if not self.builtins.subagents:
            return None
        if self.subagent_policy is not None or not self.subagents:
            return self.subagent_policy
        return SubagentPolicy()

    @field_validator("subagents")
    @classmethod
    def reject_blank_subagent_names(
        cls, value: dict[str, SubagentConfig]
    ) -> dict[str, SubagentConfig]:
        return _reject_blank_subagent_names(value)

    @model_validator(mode="before")
    @classmethod
    def reject_removed_fields(cls, data: object) -> object:
        return reject_removed_tool_selection(data, owner="AgentConfig")

    @model_validator(mode="after")
    def validate_builtins(self) -> Self:
        builtins = self.builtins
        if not builtins.skills and (self.skills or self.skill_sources):
            raise ValueError("skills and skill_sources require builtins.skills")
        if not builtins.subagents and (self.subagents or self.subagent_policy is not None):
            raise ValueError("subagents and subagent_policy require builtins.subagents")
        if builtins.programmatic_enabled:
            return self
        programmatic_only = [
            *(f"tool {tool.name!r}" for tool in self.tools if tool.model_access == "programmatic"),
            *(
                f"MCP source {name!r}"
                for name, source in self.mcps.items()
                if source.model_access == "programmatic"
            ),
        ]
        if programmatic_only:
            raise ValueError(
                "programmatic-only "
                + ", ".join(programmatic_only)
                + " requires builtins.programmatic_tool_calling; set model_access to "
                "'direct' or 'both'"
            )
        if self.connectors is not None:
            raise ValueError("connectors require builtins.programmatic_tool_calling")
        return self

    @model_validator(mode="before")
    @classmethod
    def normalize_mcp_source_discriminators(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        raw_mcps = data.get("mcps")
        if not isinstance(raw_mcps, dict):
            return data
        normalized_mcps: dict[object, object] = {}
        changed = False
        for name, value in raw_mcps.items():
            if isinstance(value, dict) and "transport" not in value:
                normalized_value = dict(value)
                normalized_value["transport"] = normalized_value.get("type", "http")
                normalized_mcps[name] = normalized_value
                changed = True
                continue
            normalized_mcps[name] = value
        if not changed:
            return data
        return {**data, "mcps": normalized_mcps}

    @model_validator(mode="after")
    def validate_skills(self) -> Self:
        if self.builtins.skills and any(tool.name == "skill" for tool in self.tools):
            raise ValueError(
                "The 'skill' tool is built in; set builtins.skills to false to declare one"
            )
        skill_names = [skill.name for skill in self.skills]
        if len(skill_names) != len(set(skill_names)):
            raise ValueError("Agent configuration contains duplicate skill names")
        materialized_skills = [
            skill for skill in self.skills if isinstance(skill, MaterializedSkillDefinition)
        ]
        if materialized_skills and len(materialized_skills) != len(self.skills):
            raise ValueError("Agent configuration cannot mix inline and materialized skills")
        if materialized_skills and self.skill_catalog_fingerprint is None:
            raise ValueError("Agent configuration with skills requires a skill catalog fingerprint")
        if (
            self.skill_catalog_fingerprint is not None
            and self.skill_catalog_fingerprint
            != calculate_skill_catalog_fingerprint(materialized_skills)
        ):
            raise ValueError("Agent configuration skill catalog fingerprint does not match skills")
        source_keys = [
            (
                source.type,
                (
                    str(source.id)
                    if isinstance(source, RegistrySkillSourceConfiguration)
                    else source.model_dump_json()
                ),
            )
            for source in self.skill_sources
        ]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("Agent configuration contains duplicate skill sources")
        return self
