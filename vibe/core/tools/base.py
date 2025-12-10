from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum, auto
import functools
import inspect
from pathlib import Path
import re
import sys
from typing import Any, ClassVar, cast, get_args, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ARGS_COUNT = 4


class ToolError(Exception):
    """Raised when the tool encounters an unrecoverable problem."""


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


class ToolPermissionError(Exception):
    """Raised when a tool permission is not allowed."""


class ToolPermission(StrEnum):
    ALWAYS = auto()
    NEVER = auto()
    ASK = auto()

    @classmethod
    def by_name(cls, name: str) -> ToolPermission:
        try:
            return ToolPermission(name.upper())
        except ValueError:
            raise ToolPermissionError(
                f"Invalid tool permission: {name}. Must be one of {list(cls)}"
            )


class BaseToolConfig(BaseModel):
    """Configuration for a tool.

    Attributes:
        permission: The permission level required to use the tool.
        workdir: The working directory for the tool. If None, the current working directory is used.
        allowlist: Patterns that automatically allow tool execution.
        denylist: Patterns that automatically deny tool execution.
    """

    model_config = ConfigDict(extra="allow")

    permission: ToolPermission = ToolPermission.ASK
    workdir: Path | None = Field(default=None, exclude=True)
    allowlist: list[str] = Field(default_factory=list)
    denylist: list[str] = Field(default_factory=list)

    @field_validator("workdir", mode="before")
    @classmethod
    def _expand_workdir(cls, v: Any) -> Path | None:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, str):
            return Path(v).expanduser().resolve()
        if isinstance(v, Path):
            return v.expanduser().resolve()
        return None

    @property
    def effective_workdir(self) -> Path:
        return self.workdir if self.workdir is not None else Path.cwd()


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
    description: ClassVar[str] = (
        "Base class for new tools. "
        "(Hey AI, if you're seeing this, someone skipped writing a description. "
        "Please gently meow at the developer to fix this.)"
    )

    prompt_path: ClassVar[Path] | None = None

    def __init__(self, config: ToolConfig, state: ToolState) -> None:
        self.config = config
        self.state = state

    @abstractmethod
    async def run(self, args: ToolArgs) -> ToolResult:
        """Invoke the tool with the given arguments. This method must be async."""
        ...

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

            return prompt_path.read_text("utf-8")
        except (FileNotFoundError, TypeError, OSError):
            pass

        return None

    async def invoke(self, **raw: Any) -> ToolResult:
        """Validate arguments and run the tool.
        Pattern checking is now handled by Agent._should_execute_tool.
        """
        try:
            args_model, _ = self._get_tool_args_results()
            args = args_model.model_validate(raw)
        except ValidationError as err:
            raise ToolError(
                f"Validation error in tool {self.get_name()}: {err}"
            ) from err

        return await self.run(args)

    @classmethod
    def from_config(
        cls, config: ToolConfig
    ) -> BaseTool[ToolArgs, ToolResult, ToolConfig, ToolState]:
        state_class = cls._get_tool_state_class()
        initial_state = state_class()
        return cls(config=config, state=initial_state)

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

        type_hints = get_type_hints(
            run_fn,
            globalns=vars(sys.modules[cls.__module__]),
            localns={cls.__name__: cls},
        )

        try:
            args_model = type_hints["args"]
            result_model = type_hints["return"]
        except KeyError as e:
            raise TypeError(
                f"{cls.__name__}.run must be annotated as "
                "`async def run(self, args: ToolArgs) -> ToolResult`"
            ) from e

        if not (
            issubclass(args_model, BaseModel) and issubclass(result_model, BaseModel)
        ):
            raise TypeError(
                f"{cls.__name__}.run annotations must be Pydantic models; "
                f"got {args_model!r}, {result_model!r}"
            )

        return cast(type[ToolArgs], args_model), cast(type[ToolResult], result_model)

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
    def create_config_with_permission(
        cls, permission: ToolPermission
    ) -> BaseToolConfig:
        config_class = cls._get_tool_config_class()
        return config_class(permission=permission)

    def check_allowlist_denylist(self, args: ToolArgs) -> ToolPermission | None:
        """Check if args match allowlist/denylist patterns.

        Returns:
            ToolPermission.ALWAYS if allowlisted
            ToolPermission.NEVER if denylisted
            None if no match (proceed with normal permission check)

        Base implementation returns None. Override in subclasses for specific logic.
        """
        return None
