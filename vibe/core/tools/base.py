from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import functools
import inspect
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, ClassVar, cast, get_args

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.tools.models import (
    PermissionScope,
    ToolPermission,
    ToolPermissionError as ToolPermissionError,
)
from vibe.core.workspace import Workspace
from vibe.permissions import OutsideWorkdirPolicy
from vibe.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.tools.permissions import PermissionContext

ARGS_COUNT = 4


class ToolError(Exception):
    """Raised when the tool encounters an unrecoverable problem.

    ``model_detail`` is appended for the model only; ``display`` stays the head.
    """

    def __init__(self, message: str, *, model_detail: str | None = None) -> None:
        super().__init__(f"{message}\n\n{model_detail}" if model_detail else message)
        self.display = message


class BaseToolConfig(BaseModel):
    """Configuration for a tool.

    Attributes:
        permission: The permission level required to use the tool.
        allowlist: Patterns that automatically allow tool execution.
        denylist: Patterns that automatically deny tool execution.
        sensitive_patterns: Patterns that trigger ASK even when permission is ALWAYS.
        outside_workdir: Per-tool override of the global ``outside_workdir`` policy.
    """

    model_config = ConfigDict(extra="allow")

    permission: ToolPermission = ToolPermission.ASK
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)
    sensitive_patterns: list[str] = Field(default_factory=list)
    outside_workdir: OutsideWorkdirPolicy | None = Field(
        default=None,
        description=(
            "Overrides the global 'outside_workdir' policy for this tool. "
            "Unset follows the global policy."
        ),
    )


class BaseToolState(BaseModel):
    model_config = ConfigDict(
        extra="forbid", validate_default=True, arbitrary_types_allowed=True
    )


class BaseTool[
    ToolArgs: BaseModel,
    ToolResult: BaseModel,
    ToolConfig: BaseToolConfig,
    ToolState: BaseToolState,
]:
    description: ClassVar[str] = ""

    prompt_path: ClassVar[Path] | None = None

    # Higher wins when several tool classes publish the same name; the active
    # variant is the available one with the greatest priority.
    selection_priority: ClassVar[int] = 0

    # Optional gate for the managed-shell GrowthBook rollout. Keep this on the
    # builtin shell tools only so custom tool availability stays config-only.
    shell_rollout: ClassVar[str | None] = None
    local_managed_shell_only: ClassVar[bool] = False

    # A shell matches its ``allowlist`` against command prefixes and a file tool
    # against paths, so an "always" grant persisted outside these scopes becomes
    # an entry the tool never reads back.
    allowlist_scopes: ClassVar[frozenset[PermissionScope]] = frozenset(PermissionScope)

    def __init__(
        self,
        config_getter: Callable[[], ToolConfig],
        state: ToolState,
        *,
        cwd: Path | None = None,
        harness_files: HarnessFilesManager | None = None,
        scratchpad_dir: Path | None = None,
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
        # project_roots omits an untrusted cwd, since config is only discovered
        # under trusted roots. for_session adds it back, so such a directory
        # stays writable without becoming a place config is read from.
        self.workspace = Workspace.for_session(
            self.cwd, self.harness_files.project_roots
        )
        self.scratchpad_dir = scratchpad_dir

    @property
    def config(self) -> ToolConfig:
        return self._config_getter()

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

    @classmethod
    def validate_arguments(cls, value: Any) -> BaseModel:
        args_model, _ = cls._get_tool_args_results()
        return args_model.model_validate(value)

    @classmethod
    def from_config(
        cls,
        config_getter: Callable[[], ToolConfig],
        *,
        cwd: Path | None = None,
        harness_files: HarnessFilesManager | None = None,
        scratchpad_dir: Path | None = None,
    ) -> BaseTool[ToolArgs, ToolResult, ToolConfig, ToolState]:
        state_class = cls._get_tool_state_class()
        initial_state = state_class()
        return cls(
            config_getter=config_getter,
            state=initial_state,
            cwd=cwd,
            harness_files=harness_files,
            scratchpad_dir=scratchpad_dir,
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
            if base_class is object:
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
            if base_class is object:
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
        for klass in cls.__mro__:
            for base in klass.__dict__.get("__orig_bases__", ()):
                if getattr(base, "__origin__", None) is not BaseTool:
                    continue
                type_args = get_args(base)
                if len(type_args) == ARGS_COUNT:
                    return (
                        cast(type[ToolArgs], type_args[0]),
                        cast(type[ToolResult], type_args[1]),
                    )

        raise TypeError(
            f"Could not determine ToolArgs/ToolResult for {cls.__name__}. "
            "Ensure it inherits from BaseTool with concrete type arguments."
        )

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

    def resolve_permission(self, args: ToolArgs) -> PermissionContext | None:
        """Per-invocation permission override, checked before config-level permission.

        Returns:
            PermissionContext with granular required_permissions and a permission
            level (ALWAYS/NEVER/ASK), or None to fall through to config permission.

        Override in subclasses for domain-specific rules (e.g. workdir checks).
        """
        return None
