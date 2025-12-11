from __future__ import annotations

from abc import ABC, abstractmethod
import functools
import inspect
from pathlib import Path
import re
import sys
from typing import (
    Any,
    ClassVar,
    Generic,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from chefchat.core.compatibility import StrEnum

ArgsT = TypeVar("ArgsT", bound=BaseModel)
ResultT = TypeVar("ResultT", bound=BaseModel)
ConfigT = TypeVar("ConfigT", bound="BaseToolConfig")
StateT = TypeVar("StateT", bound="BaseToolState")


# =======================================================
# Exceptions
# =======================================================


class ToolError(Exception):
    """Raised when a tool encounters an unrecoverable problem."""


class ToolPermissionError(Exception):
    """Raised when an invalid tool permission is requested."""


# =======================================================
# Permissions
# =======================================================


class ToolPermission(StrEnum):
    ALWAYS = "always"
    NEVER = "never"
    ASK = "ask"

    @classmethod
    def by_name(cls, name: str) -> ToolPermission:
        try:
            return cls[name.upper()]
        except KeyError:
            raise ToolPermissionError(
                f"Invalid tool permission '{name}'. Must be one of: "
                f"{', '.join(p.name for p in cls)}"
            )


# =======================================================
# Config
# =======================================================


class BaseToolConfig(BaseModel):
    """Configuration common to all tools."""

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
        raise TypeError(f"Invalid workdir type: {type(v)}")

    @property
    def effective_workdir(self) -> Path:
        """Workdir falling back to CWD."""
        return self.workdir or Path.cwd()


# =======================================================
# State
# =======================================================


class BaseToolState(BaseModel):
    """Internal persistent state for a tool instance."""

    model_config = ConfigDict(
        extra="forbid", validate_default=True, arbitrary_types_allowed=True
    )


# =======================================================
# ToolInfo
# =======================================================


class ToolInfo(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


# =======================================================
# BaseTool Generic Framework
# =======================================================


class BaseTool(ABC, Generic[ArgsT, ResultT, ConfigT, StateT]):
    """The main base class for defining async tools.
    Subclasses must specify four type parameters:

        BaseTool[ArgsModel, ResultModel, ConfigModel, StateModel]
    """

    description: ClassVar[str] = (
        "Base tool — developer forgot to write a description. Please meow gently."
    )

    prompt_path: ClassVar[Path | None] = None

    def __init__(self, config: ConfigT, state: StateT) -> None:
        self.config = config
        self.state = state

    # ---------------------------------------------------
    # Required override for tools
    # ---------------------------------------------------
    @abstractmethod
    async def run(self, args: ArgsT) -> ResultT: ...

    # ---------------------------------------------------
    # Loading external prompt files
    # ---------------------------------------------------
    @classmethod
    @functools.cache
    def get_tool_prompt(cls) -> str | None:
        """Return the contents of a prompt file if available."""
        try:
            class_file = inspect.getfile(cls)
            class_path = Path(class_file)
        except Exception:
            return None

        prompt_dir = class_path.parent / "prompts"
        prompt_path = cls.prompt_path or prompt_dir / (class_path.stem + ".md")

        if prompt_path.exists():
            return prompt_path.read_text("utf-8")
        return None

    # ---------------------------------------------------
    # Safety Checks
    # ---------------------------------------------------
    def check_allowlist_denylist(self, args: ArgsT) -> ToolPermission:
        """Check if the arguments are allowed or denied by the configuration.
        Default implementation returns ASK (neutral).
        Subclasses can override this to implement granular checks.
        """
        return ToolPermission.ASK

    # ---------------------------------------------------
    # Argument validation + execution
    # ---------------------------------------------------
    async def invoke(self, **raw: Any) -> ResultT:
        try:
            Args, _ = self._get_args_and_result_models()
            parsed = Args.model_validate(raw)
        except ValidationError as err:
            raise ToolError(
                f"Argument validation failed for tool '{self.get_name()}': {err}"
            ) from err

        return await self.run(parsed)

    # ---------------------------------------------------
    # Type-model extraction
    # ---------------------------------------------------
    @classmethod
    def _get_args_and_result_models(cls) -> tuple[type[ArgsT], type[ResultT]]:
        """Extract annotated `args` parameter type and return type from the run(...)
        method. Works with postponed annotations.
        """
        try:
            hints = get_type_hints(
                cls.run,
                globalns=vars(sys.modules[cls.__module__]),
                localns={cls.__name__: cls},
            )
        except Exception as e:
            raise TypeError(f"Failed resolving annotations for {cls.__name__}.run: {e}")

        if "args" not in hints or "return" not in hints:
            raise TypeError(
                f"{cls.__name__}.run must be annotated as "
                f"`async def run(self, args: ArgsModel) -> ResultModel`"
            )

        Args = hints["args"]
        Result = hints["return"]

        if not issubclass(Args, BaseModel):
            raise TypeError(f"Args model must inherit BaseModel; got {Args!r}")

        if not issubclass(Result, BaseModel):
            raise TypeError(f"Result model must inherit BaseModel; got {Result!r}")

        return cast(type[ArgsT], Args), cast(type[ResultT], Result)

    # ---------------------------------------------------
    # Config and State extraction from generics
    # ---------------------------------------------------
    @classmethod
    def _get_config_class(cls) -> type[ConfigT]:
        return cls._extract_generic_param(index=2, expected=BaseToolConfig)

    @classmethod
    def _get_state_class(cls) -> type[StateT]:
        return cls._extract_generic_param(index=3, expected=BaseToolState)

    @classmethod
    def _extract_generic_param(cls, index: int, expected: type) -> type:
        """Extracts one of the generic type parameters of BaseTool[T1, T2, T3, T4]."""
        for base in cls.__orig_bases__:  # type: ignore
            origin = get_origin(base)
            if origin is BaseTool:
                args = get_args(base)
                if len(args) != 4:
                    continue
                param = args[index]
                if not issubclass(param, expected):
                    raise TypeError(
                        f"{cls.__name__} generic parameter {index} must subclass {expected.__name__}, "
                        f"got {param}"
                    )
                return param
        raise TypeError(
            f"{cls.__name__} must inherit BaseTool[Args, Result, Config, State] "
            f"with all four generics specified."
        )

    # ---------------------------------------------------
    # Instance creation helpers
    # ---------------------------------------------------
    @classmethod
    def from_config(cls, config: ConfigT) -> BaseTool[ArgsT, ResultT, ConfigT, StateT]:
        state = cls._get_state_class()()
        return cls(config=config, state=state)

    @classmethod
    def create_config_with_permission(cls, permission: ToolPermission) -> ConfigT:
        ConfigClass = cls._get_config_class()
        return ConfigClass(permission=permission)

    # ---------------------------------------------------
    # Introspection helpers
    # ---------------------------------------------------
    @classmethod
    def get_parameters(cls) -> dict[str, Any]:
        """Return the Pydantic JSON schema for argument model, normalized."""
        Args, _ = cls._get_args_and_result_models()
        schema = Args.model_json_schema()

        schema.pop("title", None)
        schema.pop("description", None)

        if "properties" in schema:
            for prop in schema["properties"].values():
                prop.pop("title", None)

        if "$defs" in schema:
            for d in schema["$defs"].values():
                d.pop("title", None)
                if "properties" in d:
                    for prop in d["properties"].values():
                        prop.pop("title", None)

        return schema

    @classmethod
    def get_name(cls) -> str:
        name = cls.__name__
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
