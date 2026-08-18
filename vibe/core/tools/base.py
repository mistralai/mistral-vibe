from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field, replace
import functools
import inspect
from pathlib import Path
import re
import types
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Protocol,
    Union,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.checkpoints import FileSnapshot, FileState
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.tools.models import (
    ToolPermission,
    ToolPermissionError as ToolPermissionError,
)
from vibe.core.tools.terminal_runtime import TerminalRuntime
from vibe.core.types import ToolStreamEvent
from vibe.observability.logging import logger
from vibe.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.agents.manager import AgentManager
    from vibe.core.config import VibeConfigSchema
    from vibe.core.hooks.models import HookConfigResult
    from vibe.core.skills.manager import SkillManager
    from vibe.core.subagents import SubagentRunnerPort
    from vibe.core.telemetry.types import LaunchContext
    from vibe.core.tools.io_port import ToolIOPort
    from vibe.core.tools.mcp.pool import MCPConnectionPool
    from vibe.core.tools.mcp_sampling import MCPSamplingHandler
    from vibe.core.tools.models import RequiredPermission
    from vibe.core.tools.permissions import PermissionContext, PermissionStore
    from vibe.core.types import (
        ApprovalResponse,
        ClearContextCallback,
        SwitchAgentCallback,
    )

ARGS_COUNT = 4


class InteractionRequestPort(Protocol):
    async def request_approval(
        self,
        tool_name: str,
        args: BaseModel,
        tool_call_id: str,
        required_permissions: list[RequiredPermission] | None,
    ) -> tuple[ApprovalResponse, str | None]: ...

    async def request_user_input(
        self, args: BaseModel, tool_call_id: str
    ) -> BaseModel: ...


@dataclass
class InvokeContext:
    """Context passed to tools during invocation."""

    tool_call_id: str
    agent_manager: AgentManager | None = field(default=None)
    interaction_requests: InteractionRequestPort | None = field(default=None)
    subagent_runner: SubagentRunnerPort | None = field(default=None)
    sampling_callback: MCPSamplingHandler | None = field(default=None)
    session_dir: Path | None = field(default=None)
    launch_context: LaunchContext | None = field(default=None)
    plan_file_path: Path | None = field(default=None)
    switch_agent_callback: SwitchAgentCallback | None = field(default=None)
    request_clear_context_callback: ClearContextCallback | None = field(default=None)
    skill_manager: SkillManager | None = field(default=None)
    is_skill_loaded: Callable[[str], bool] | None = field(default=None)
    scratchpad_dir: Path | None = field(default=None)
    permission_store: PermissionStore | None = field(default=None)
    hook_config_result: HookConfigResult | None = field(default=None)
    session_id: str | None = field(default=None)
    mcp_pool: MCPConnectionPool | None = field(default=None)
    tool_io: ToolIOPort | None = field(default=None)


class ToolError(Exception):
    """Raised when the tool encounters an unrecoverable problem.

    ``model_detail`` is appended for the model only; ``display`` stays the head.
    """

    def __init__(self, message: str, *, model_detail: str | None = None) -> None:
        super().__init__(f"{message}\n\n{model_detail}" if model_detail else message)
        self.display = message


class CancellableToolResult(BaseModel):
    cancelled: bool = Field(
        default=False,
        description="True if the user cancelled the tool without completing it.",
    )


class ToolInfo(BaseModel):
    """Information about a tool.

    Attributes:
        name: The name of the tool.
        description: A brief description of what the tool does.
        parameters: A dictionary of parameters required by the tool.
    """

    name: str
    description: str
    parameters: dict[str, Any]


class BaseToolConfig(BaseModel):
    """Configuration for a tool.

    Attributes:
        permission: The permission level required to use the tool.
        allowlist: Patterns that automatically allow tool execution.
        denylist: Patterns that automatically deny tool execution.
        sensitive_patterns: Patterns that trigger ASK even when permission is ALWAYS.
    """

    model_config = ConfigDict(extra="allow")

    permission: ToolPermission = ToolPermission.ASK
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)
    sensitive_patterns: list[str] = Field(default_factory=list)


class BaseToolState(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_default=True, arbitrary_types_allowed=True
    )


class BaseTool[
    ToolArgs: BaseModel,
    ToolResult: BaseModel,
    ToolConfig: BaseToolConfig,
    ToolState: BaseToolState,
](ABC):
    description: ClassVar[str] = ""

    prompt_path: ClassVar[Path] | None = None

    # Higher wins when several tool classes publish the same name; the active
    # variant is the available one with the greatest priority.
    selection_priority: ClassVar[int] = 0

    # Optional gate for the managed-shell GrowthBook rollout. Keep this on the
    # builtin shell tools only so custom tool availability stays config-only.
    shell_rollout: ClassVar[str | None] = None
    local_managed_shell_only: ClassVar[bool] = False

    def __init__(
        self,
        config_getter: Callable[[], ToolConfig],
        state: ToolState,
        *,
        cwd: Path | None = None,
        harness_files: HarnessFilesManager | None = None,
        scratchpad_dir: Path | None = None,
        terminal_runtime: TerminalRuntime | None = None,
    ) -> None:
        self._config_getter = config_getter
        self.state = state
        self.cwd = (cwd or Path.cwd()).resolve()
        if harness_files is None:
            try:
                harness_files = get_harness_files_manager()
            except RuntimeError:
                harness_files = HarnessFilesManager(sources=(), cwd=self.cwd)
        self.harness_files = replace(harness_files, cwd=self.cwd)
        self.scratchpad_dir = scratchpad_dir
        self.terminal_runtime = terminal_runtime or TerminalRuntime()

    @property
    def config(self) -> ToolConfig:
        return self._config_getter()

    @abstractmethod
    async def run(
        self, args: ToolArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | ToolResult, None]:
        """Invoke the tool with the given arguments."""
        raise NotImplementedError  # pragma: no cover
        yield  # type: ignore[misc]

    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        """Loads and returns the content of the tool's .md prompt file, if it exists.

        The prompt file is expected to be in a 'prompts' subdirectory relative to
        the tool's source file, with the same name but a .md extension
        (e.g., bash.py -> prompts/bash.md).
        """
        try:
            class_file = inspect.getfile(cls)
            class_path = Path(class_file)
            prompt_dir = class_path.parent / "prompts"
            prompt_path = cls.prompt_path or prompt_dir / f"{class_path.stem}.md"

            return read_safe(prompt_path).text
        except (FileNotFoundError, TypeError, OSError):
            pass

        return None

    @classmethod
    def get_full_description(cls) -> str:
        """The tool-definition description: the .md prompt if present.

        Builtin tools keep their description in a sibling ``prompts/<tool>.md``
        file. Tools without one (e.g. MCP/connector tools) fall back to the
        ``description`` ClassVar they set dynamically.
        """
        return cls.get_tool_prompt() or cls.description

    async def invoke(
        self, ctx: InvokeContext | None = None, **raw: Any
    ) -> AsyncGenerator[ToolStreamEvent | ToolResult, None]:
        """Validate arguments and run the tool."""
        try:
            args_model, _ = self._get_tool_args_results()
            args = args_model.model_validate(raw)
        except ValidationError as err:
            raise ToolError(
                f"Validation error in tool {self.get_name()}: {err}"
            ) from err

        async for item in self.run(args, ctx):
            yield item

    @classmethod
    def validate_arguments(cls, value: Any) -> BaseModel:
        args_model, _ = cls._get_tool_args_results()
        return args_model.model_validate(value)

    @classmethod
    def validate_result(cls, value: Any) -> BaseModel:
        _, result_model = cls._get_tool_args_results()
        return result_model.model_validate(value)

    @classmethod
    def from_config(
        cls,
        config_getter: Callable[[], ToolConfig],
        *,
        cwd: Path | None = None,
        harness_files: HarnessFilesManager | None = None,
        scratchpad_dir: Path | None = None,
        terminal_runtime: TerminalRuntime | None = None,
    ) -> BaseTool[ToolArgs, ToolResult, ToolConfig, ToolState]:
        state_class = cls._get_tool_state_class()
        initial_state = state_class()
        return cls(
            config_getter=config_getter,
            state=initial_state,
            cwd=cwd,
            harness_files=harness_files,
            scratchpad_dir=scratchpad_dir,
            terminal_runtime=terminal_runtime,
        )

    @classmethod
    def _get_tool_config_class(cls) -> type[ToolConfig]:
        for base in getattr(cls, "__orig_bases__", ()):
            if getattr(base, "__origin__", None) is BaseTool:
                type_args = get_args(base)
                if len(type_args) == ARGS_COUNT:
                    config_model = type_args[2]
                    if issubclass(config_model, BaseToolConfig):
                        return cast(type[ToolConfig], config_model)

        for base_class in cls.__bases__:
            if base_class is object or base_class is ABC:
                continue
            try:
                return base_class._get_tool_config_class()
            except (TypeError, AttributeError):
                continue

        raise TypeError(
            f"Could not determine ToolConfig for {cls.__name__}. "
            "Ensure it inherits from BaseTool with concrete type arguments."
        )

    @classmethod
    def _get_tool_state_class(cls) -> type[ToolState]:
        for base in getattr(cls, "__orig_bases__", ()):
            if getattr(base, "__origin__", None) is BaseTool:
                type_args = get_args(base)
                if len(type_args) == ARGS_COUNT:
                    state_model = type_args[3]
                    if issubclass(state_model, BaseToolState):
                        return cast(type[ToolState], state_model)

        for base_class in cls.__bases__:
            if base_class is object or base_class is ABC:
                continue
            try:
                return base_class._get_tool_state_class()
            except (TypeError, AttributeError):
                continue

        raise TypeError(
            f"Could not determine ToolState for {cls.__name__}. "
            "Ensure it inherits from BaseTool with concrete type arguments."
        )

    @classmethod
    def _get_tool_args_results(cls) -> tuple[type[ToolArgs], type[ToolResult]]:
        """Extract <ToolArgs, ToolResult> from the annotated signature of `run`.
        Works even when `from __future__ import annotations` is in effect.
        """
        run_fn = cls.run.__func__ if isinstance(cls.run, classmethod) else cls.run

        # `get_type_hints` resolves against `run_fn.__globals__`, which is the
        # module that wrote the annotation even when a subclass inherits `run`.
        type_hints = get_type_hints(run_fn)

        try:
            args_model = type_hints["args"]
            return_annotation = type_hints["return"]
        except KeyError as e:
            raise TypeError(
                f"{cls.__name__}.run must be annotated with args and return type"
            ) from e

        result_model = cls._extract_result_type(return_annotation)

        if not issubclass(args_model, BaseModel):
            raise TypeError(
                f"{cls.__name__}.run args annotation must be a Pydantic model; "
                f"got {args_model!r}"
            )

        if not issubclass(result_model, BaseModel):
            raise TypeError(
                f"{cls.__name__}.run must yield a Pydantic model as result; "
                f"got {result_model!r}"
            )

        return cast(type[ToolArgs], args_model), cast(type[ToolResult], result_model)

    @classmethod
    def _extract_result_type(cls, return_annotation: Any) -> type:
        """Extract the ToolResult type from AsyncGenerator[ToolStreamEvent | ToolResult, None]."""
        origin = get_origin(return_annotation)
        if origin is not AsyncGenerator:
            if isinstance(return_annotation, type):
                return return_annotation
            raise TypeError(f"Could not extract result type from {return_annotation!r}")

        gen_args = get_args(return_annotation)
        if not gen_args:
            raise TypeError(f"Could not extract result type from {return_annotation!r}")

        yield_type = gen_args[0]
        yield_origin = get_origin(yield_type)

        # Handle Union types (X | Y or Union[X, Y])
        if yield_origin is Union or isinstance(yield_type, types.UnionType):
            for arg in get_args(yield_type):
                if arg is not ToolStreamEvent and isinstance(arg, type):
                    return arg

        # Handle single type
        if isinstance(yield_type, type):
            return yield_type

        raise TypeError(f"Could not extract result type from {return_annotation!r}")

    @classmethod
    def get_parameters(cls) -> dict[str, Any]:
        """Return a cleaned-up JSON-schema dict describing the arguments model
        with which this concrete tool was parametrised.
        """
        args_model, _ = cls._get_tool_args_results()
        schema = args_model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)

        if "properties" in schema:
            for prop_details in schema["properties"].values():
                prop_details.pop("title", None)

        if "$defs" in schema:
            for def_details in schema["$defs"].values():
                def_details.pop("title", None)
                if "properties" in def_details:
                    for prop_details in def_details["properties"].values():
                        prop_details.pop("title", None)

        return schema

    @classmethod
    def get_name(cls) -> str:
        name = cls.__name__
        snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
        return snake_case

    @classmethod
    def is_available(cls, config: VibeConfigSchema | None = None) -> bool:
        return True

    @classmethod
    def create_config_with_permission(
        cls, permission: ToolPermission
    ) -> BaseToolConfig:
        config_class = cls._get_tool_config_class()
        return config_class(permission=permission)

    def resolve_permission(self, args: ToolArgs) -> PermissionContext | None:
        """Per-invocation permission override, checked before config-level permission.

        Returns:
            PermissionContext with granular required_permissions and a permission
            level (ALWAYS/NEVER/ASK), or None to fall through to config permission.

        Override in subclasses for domain-specific rules (e.g. workdir checks).
        """
        return None

    def get_file_snapshot(self, args: ToolArgs) -> FileSnapshot | None:
        """Return a snapshot of the file this tool is about to modify.

        Called before ``run()`` so the checkpoint system can capture
        the file's state *before* the tool writes to it.
        Override in tools that modify files on disk.
        """
        return None

    def get_file_snapshot_for_path(self, path: str) -> FileSnapshot:
        file_path = Path(path).expanduser()
        if not file_path.is_absolute():
            file_path = self.cwd / file_path
        file_path = file_path.resolve()
        try:
            content: bytes | None = file_path.read_bytes()
        except FileNotFoundError:
            content = None
        except Exception:
            logger.warning("Failed to read file for tool snapshot: %s", file_path)
            content = None
        return FileSnapshot(path=str(file_path), state=FileState(content))

    def get_result_extra(self, result: ToolResult) -> str | None:
        """Optional extra context appended to the result text sent to the LLM.

        Override in subclasses to inject contextual information alongside
        tool results (e.g. directory-level instructions discovered during
        file reads).  The default returns ``None`` (no annotation).
        """
        return None
