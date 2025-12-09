from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class ToolCallDisplay(BaseModel):
    summary: str  # Brief description: "Writing file.txt", "Patching code.py"
    content: str | None = None  # Optional content preview
    details: dict[str, Any] = Field(default_factory=dict)  # Tool-specific data


class ToolResultDisplay(BaseModel):
    success: bool
    message: str
    warnings: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)  # Tool-specific data


@runtime_checkable
class ToolUIData[TArgs: BaseModel, TResult: BaseModel](Protocol):
    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay: ...

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay: ...

    @classmethod
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

        args_dict = event.args.model_dump() if hasattr(event.args, "model_dump") else {}
        args_str = ", ".join(f"{k}={v!r}" for k, v in list(args_dict.items())[:3])
        return ToolCallDisplay(
            summary=f"{event.tool_name}({args_str})", details=args_dict
        )

    def get_result_display(self, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)

        if event.skipped:
            return ToolResultDisplay(
                success=False, message=event.skip_reason or "Skipped"
            )

        if self.ui_data_class:
            return self.ui_data_class.get_result_display(event)

        return ToolResultDisplay(
            success=True,
            message="Success",
            details=(
                event.result.model_dump()
                if event.result and hasattr(event.result, "model_dump")
                else {}
            ),
        )

    def get_status_text(self) -> str:
        if self.ui_data_class:
            return self.ui_data_class.get_status_text()

        tool_name = getattr(self.tool_class, "get_name", lambda: "tool")()
        return f"Running {tool_name}"
