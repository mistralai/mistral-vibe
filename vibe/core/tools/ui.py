from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolCallDisplay(BaseModel):
    summary: str  # Brief description: "Writing file.txt", "Patching code.py"
    content: str | None = None  # Optional content preview


class ToolResultDisplay(BaseModel):
    success: bool
    message: str
    warnings: list[str] = Field(default_factory=list)


class ToolUIData[TArgs: BaseModel, TResult: BaseModel](ABC):
    @classmethod
    def _display_name(cls) -> str:
        get_name = cast(Callable[[], str] | None, getattr(cls, "get_name", None))
        return get_name() if get_name is not None else cls.__name__.lower()

    @classmethod
    def get_no_args_display(cls) -> ToolCallDisplay:
        return ToolCallDisplay(summary=cls._display_name())

    @classmethod
    def get_invalid_args_display(cls) -> ToolCallDisplay:
        return ToolCallDisplay(summary="Invalid Arguments")

    @classmethod
    def format_call_display(cls, args: TArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=cls._display_name())

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if event.args is None:
            return cls.get_no_args_display()

        introspect = cast(
            Callable[[], tuple[type, ...]] | None,
            getattr(cls, "_get_tool_args_results", None),
        )
        if introspect is not None:
            expected_type = introspect()[0]
            if not isinstance(event.args, expected_type):
                return cls.get_invalid_args_display()

        return cls.format_call_display(cast(TArgs, event.args))

    @classmethod
    @abstractmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay: ...

    @classmethod
    @abstractmethod
    def get_status_text(cls) -> str: ...


class ToolUIDataAdapter:
    def __init__(self, tool_class: Any) -> None:
        self.tool_class = tool_class
        self.ui_data_class: type[ToolUIData[Any, Any]] | None = (
            tool_class if issubclass(tool_class, ToolUIData) else None
        )

    def get_call_display(self, event: ToolCallEvent) -> ToolCallDisplay:
        if self.ui_data_class:
            return self.ui_data_class.get_call_display(event)

        args_dict = (
            event.args.model_dump()
            if event.args and hasattr(event.args, "model_dump")
            else {}
        )
        args_str = ", ".join(f"{k}={v!r}" for k, v in list(args_dict.items())[:3])
        return ToolCallDisplay(summary=f"{event.tool_name}({args_str})")

    def get_result_display(self, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)

        if event.skipped:
            return ToolResultDisplay(
                success=False, message=event.skip_reason or "Skipped"
            )

        if self.ui_data_class:
            return self.ui_data_class.get_result_display(event)

        return ToolResultDisplay(success=True, message="Success")

    def get_status_text(self) -> str:
        if self.ui_data_class:
            return self.ui_data_class.get_status_text()

        tool_name = getattr(self.tool_class, "get_name", lambda: "tool")()
        return f"Running {tool_name}"
