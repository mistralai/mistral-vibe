from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from pydantic import BaseModel, JsonValue

from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    EffectDetail,
    EffectResultDisplay,
    EffectState,
    FailedEffectState,
    FileEditEffectDetail,
    FileEditEffectInput,
    FileEditEffectOutput,
    FileReadEffectDetail,
    FileReadEffectInput,
    FileReadEffectOutput,
    FileSearchEffectDetail,
    FileSearchEffectInput,
    FileSearchEffectOutput,
    FileWriteEffectDetail,
    FileWriteEffectInput,
    FileWriteEffectOutput,
    GenericEffectDetail,
    PublicError,
    ShellEffectDetail,
    ShellEffectInput,
    ShellEffectOutput,
    SkillEffectDetail,
    SkillEffectInput,
    SkillEffectOutput,
    SkippedEffectState,
    SubagentEffectDetail,
    SubagentEffectInput,
    SubagentEffectOutput,
    TodoEffectDetail,
    TodoEffectInput,
    TodoEffectOutput,
    UserQuestionEffectDetail,
    UserQuestionRequest,
    UserQuestionResult,
    WebFetchEffectDetail,
    WebFetchEffectInput,
    WebFetchEffectOutput,
    WebSearchEffectDetail,
    WebSearchEffectInput,
    WebSearchEffectOutput,
)
from vibe.core.types import ToolResultEvent
from vibe.core.utils import TaggedText
from vibe.utils.tool_presentation import ToolCallPresentation, ToolEffectKind


@dataclass(frozen=True, slots=True)
class _EffectProjection:
    detail_model: type[BaseModel]
    input_model: type[BaseModel]
    output_model: type[BaseModel]


_EFFECT_PROJECTIONS = {
    ToolEffectKind.SHELL: _EffectProjection(
        ShellEffectDetail, ShellEffectInput, ShellEffectOutput
    ),
    ToolEffectKind.FILE_EDIT: _EffectProjection(
        FileEditEffectDetail, FileEditEffectInput, FileEditEffectOutput
    ),
    ToolEffectKind.FILE_SEARCH: _EffectProjection(
        FileSearchEffectDetail, FileSearchEffectInput, FileSearchEffectOutput
    ),
    ToolEffectKind.FILE_READ: _EffectProjection(
        FileReadEffectDetail, FileReadEffectInput, FileReadEffectOutput
    ),
    ToolEffectKind.TODO: _EffectProjection(
        TodoEffectDetail, TodoEffectInput, TodoEffectOutput
    ),
    ToolEffectKind.FILE_WRITE: _EffectProjection(
        FileWriteEffectDetail, FileWriteEffectInput, FileWriteEffectOutput
    ),
    ToolEffectKind.USER_QUESTION: _EffectProjection(
        UserQuestionEffectDetail, UserQuestionRequest, UserQuestionResult
    ),
    ToolEffectKind.WEB_SEARCH: _EffectProjection(
        WebSearchEffectDetail, WebSearchEffectInput, WebSearchEffectOutput
    ),
    ToolEffectKind.WEB_FETCH: _EffectProjection(
        WebFetchEffectDetail, WebFetchEffectInput, WebFetchEffectOutput
    ),
    ToolEffectKind.SKILL: _EffectProjection(
        SkillEffectDetail, SkillEffectInput, SkillEffectOutput
    ),
    ToolEffectKind.SUBAGENT: _EffectProjection(
        SubagentEffectDetail, SubagentEffectInput, SubagentEffectOutput
    ),
}


def project_effect_detail(
    tool_name: str, value: BaseModel | JsonValue, presentation: ToolCallPresentation
) -> EffectDetail:
    projection = _EFFECT_PROJECTIONS.get(presentation.kind)
    if projection is None:
        return GenericEffectDetail(
            tool_name=tool_name, input=_dump_value(value), display=presentation.display
        )
    return cast(
        EffectDetail,
        projection.detail_model(
            tool_name=tool_name,
            input=_project_model(projection.input_model, value),
            display=presentation.display,
        ),
    )


def project_effect_state(
    event: ToolResultEvent, *, output_text: str = ""
) -> EffectState:
    display = _result_display(event)
    duration_ms = (event.duration or 0.0) * 1000
    if event.cancelled:
        return CancelledEffectState(
            reason=event.error or "Cancelled",
            output_text=output_text,
            duration_ms=duration_ms,
            display=display,
        )
    if event.skipped:
        return SkippedEffectState(
            reason=event.skip_reason or "Skipped", display=display
        )
    if event.error:
        return FailedEffectState(
            error=PublicError(message=TaggedText.from_string(event.error).message),
            output_text=output_text,
            duration_ms=duration_ms,
            display=display,
        )
    return CompletedEffectState(
        output=project_effect_output(event),
        output_text=output_text,
        duration_ms=duration_ms,
        display=display,
    )


def project_effect_output(event: ToolResultEvent) -> JsonValue:
    presentation = event.presentation
    result = (
        presentation.projected_output
        if presentation is not None and presentation.projected_output is not None
        else event.result
    )
    if result is None:
        return None
    if presentation is None:
        raise ValueError("Successful tool results require a presentation snapshot")
    return project_effect_output_value(presentation.kind, result)


def project_effect_output_value(
    kind: ToolEffectKind, value: BaseModel | JsonValue
) -> JsonValue:
    projection = _EFFECT_PROJECTIONS.get(kind)
    if projection is None:
        return _dump_value(value)
    projected = _project_model(projection.output_model, value)
    return _dump_value(projected)


def _project_model[ModelT: BaseModel](
    model_type: type[ModelT], value: object | None
) -> ModelT | None:
    if value is None:
        return None
    if isinstance(value, dict):
        projected: dict[str, object] = {}
        for name, field in model_type.model_fields.items():
            if name in value:
                projected[name] = value[name]
                continue
            if isinstance(field.alias, str) and field.alias in value:
                projected[field.alias] = value[field.alias]
        value = projected
    return model_type.model_validate(value, from_attributes=True)


def _dump_value(value: BaseModel | JsonValue) -> JsonValue:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return cast(JsonValue, value.model_dump(mode="json", by_alias=True))
    return value


def _result_display(event: ToolResultEvent) -> EffectResultDisplay:
    if event.error:
        return EffectResultDisplay(
            success=False, message=TaggedText.from_string(event.error).message
        )
    if event.skipped:
        return EffectResultDisplay(
            success=False,
            message=TaggedText.from_string(event.skip_reason or "Skipped").message,
        )
    if event.presentation is not None:
        return event.presentation.display
    if event.cancelled:
        return EffectResultDisplay(success=False, message=event.error or "Cancelled")
    raise ValueError("Successful tool results require a presentation snapshot")
