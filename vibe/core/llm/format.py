from __future__ import annotations

from collections.abc import Sequence
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.tools.base import BaseTool
from vibe.core.types import (
    AvailableTool,
    FunctionCall,
    LLMMessage,
    Role,
    StrToolChoice,
    ToolCall,
)
from vibe.core.utils.tags import CancellationReason, get_user_cancellation_message

if TYPE_CHECKING:
    from vibe.core.tools.manager import ToolManager

_BRIDGE_ASSISTANT_CONTENT = "."


def _is_empty_assistant(message: LLMMessage) -> bool:
    return (
        message.role == Role.assistant
        and not (message.content or "").strip()
        and not message.tool_calls
        and not (message.reasoning_content or "").strip()
    )


def _pair_orphaned_tool_results(messages: list[LLMMessage]) -> list[LLMMessage]:
    """Give tool results whose assistant call was dropped a call turn.

    Compaction keeps the tail of history, so a tool result whose call sat just
    before the boundary can follow a user message. Mistral Small 4's role-order
    template rejects ``user`` -> ``tool``; the synthesized call turn carries the
    ids and names the results already declare, so no response is invented.
    """
    paired: list[LLMMessage] = []
    declared: set[str] = set()
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == Role.assistant:
            declared = {tool_call.id or "" for tool_call in message.tool_calls or []}
            paired.append(message)
            index += 1
            continue
        if message.role == Role.tool and (message.tool_call_id or "") not in declared:
            orphaned: list[LLMMessage] = []
            while (
                index < len(messages)
                and messages[index].role == Role.tool
                and (messages[index].tool_call_id or "") not in declared
            ):
                orphaned.append(messages[index])
                index += 1
            paired.append(
                LLMMessage(
                    role=Role.assistant,
                    content="",
                    tool_calls=[
                        ToolCall(
                            id=result.tool_call_id or "",
                            index=position,
                            function=FunctionCall(
                                name=result.name or "tool", arguments="{}"
                            ),
                        )
                        for position, result in enumerate(orphaned)
                    ],
                )
            )
            paired.extend(orphaned)
            declared = {result.tool_call_id or "" for result in orphaned}
            continue
        paired.append(message)
        index += 1
    return paired


def missing_tool_response_insertions(
    messages: Sequence[LLMMessage],
) -> list[tuple[int, LLMMessage]]:
    """Synthesized responses for tool calls that never got one.

    Returns ``(insert_at, message)`` pairs in order; callers apply them to
    whatever container they own (a plain list or an observable MessageList).
    """
    insertions: list[tuple[int, LLMMessage]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role != Role.assistant or not message.tool_calls:
            index += 1
            continue

        responded_ids: set[str] = set()
        next_index = index + 1
        while next_index < len(messages) and messages[next_index].role == Role.tool:
            tool_call_id = messages[next_index].tool_call_id
            if tool_call_id is not None:
                responded_ids.add(tool_call_id)
            next_index += 1

        insertion_point = next_index
        for tool_call in message.tool_calls:
            if (tool_call.id or "") in responded_ids:
                continue
            insertions.append((
                insertion_point,
                LLMMessage(
                    role=Role.tool,
                    tool_call_id=tool_call.id or "",
                    name=(
                        (tool_call.function.name or "") if tool_call.function else ""
                    ),
                    content=str(
                        get_user_cancellation_message(
                            CancellationReason.TOOL_NO_RESPONSE
                        )
                    ),
                ),
            ))
            insertion_point += 1

        index = next_index
    return insertions


def fill_missing_tool_responses(messages: list[LLMMessage]) -> list[LLMMessage]:
    filled = list(messages)
    for offset, (at, synthesized) in enumerate(
        missing_tool_response_insertions(filled)
    ):
        filled.insert(at + offset, synthesized)
    return filled


def normalize_messages_for_chat_template(
    messages: Sequence[LLMMessage],
) -> list[LLMMessage]:
    """Normalize history for strict chat templates (Mistral Jinja / llama.cpp).

    Middleware and synthetic tool-call rounds can leave consecutive user turns,
    missing tool responses, or a user message immediately after tool results.
    Those shapes break templates that require alternating user/assistant roles.
    """
    normalized = [
        message.model_copy(deep=True)
        for message in messages
        if not _is_empty_assistant(message)
    ]
    normalized = _pair_orphaned_tool_results(normalized)
    normalized = fill_missing_tool_responses(normalized)

    merged: list[LLMMessage] = []
    for message in normalized:
        if (
            merged
            and message.role == Role.user
            and merged[-1].role == Role.user
            and not merged[-1].images
            and not message.images
        ):
            # One message cannot represent two turns' text/image interleaving.
            merged[-1] = merged[-1].model_copy(
                update={
                    "content": (merged[-1].content or "")
                    + "\n\n"
                    + (message.content or "")
                }
            )
            continue
        if (
            merged
            and message.role == Role.assistant
            and merged[-1].role == Role.assistant
            and not merged[-1].tool_calls
            and not message.tool_calls
        ):
            merged[-1] += message
            continue
        merged.append(message)

    bridged: list[LLMMessage] = []
    index = 0
    while index < len(merged):
        message = merged[index]
        if message.role != Role.tool:
            bridged.append(message)
            index += 1
            continue

        while index < len(merged) and merged[index].role == Role.tool:
            bridged.append(merged[index])
            index += 1

        if index < len(merged) and merged[index].role == Role.user:
            bridged.append(
                LLMMessage(
                    role=Role.assistant,
                    content=_BRIDGE_ASSISTANT_CONTENT,
                    injected=True,
                )
            )

    # Devstral rejects an empty assistant bridge.
    alternated: list[LLMMessage] = []
    for message in bridged:
        if (
            alternated
            and message.role == Role.user
            and alternated[-1].role == Role.user
        ):
            alternated.append(
                LLMMessage(
                    role=Role.assistant,
                    content=_BRIDGE_ASSISTANT_CONTENT,
                    injected=True,
                )
            )
        alternated.append(message)
    return alternated


class ParsedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    raw_args: dict[str, Any]
    call_id: str = ""


class ResolvedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    tool_name: str
    tool_class: type[BaseTool]
    validated_args: BaseModel
    call_id: str = ""

    @property
    def args_dict(self) -> dict[str, Any]:
        return self.validated_args.model_dump()


class FailedToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_name: str
    call_id: str
    error: str


class ParsedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_calls: list[ParsedToolCall]


class ResolvedMessage(BaseModel):
    model_config = ConfigDict(frozen=True)
    tool_calls: list[ResolvedToolCall]
    failed_calls: list[FailedToolCall] = Field(default_factory=list)


class APIToolFormatHandler:
    @property
    def name(self) -> str:
        return "api"

    def get_available_tools(self, tool_manager: ToolManager) -> list[AvailableTool]:
        return [
            AvailableTool(function=fn) for fn in tool_manager.available_tool_specs()
        ]

    def get_tool_choice(self) -> StrToolChoice | AvailableTool:
        return "auto"

    def process_api_response_message(self, message: Any) -> LLMMessage:
        clean_message = {
            "role": message.role,
            "content": message.content,
            "reasoning_content": getattr(message, "reasoning_content", None),
            "reasoning_payloads": getattr(message, "reasoning_payloads", None),
        }

        if message.tool_calls:
            clean_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "index": tc.index,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return LLMMessage.model_validate(clean_message)

    def parse_message(self, message: LLMMessage) -> ParsedMessage:
        tool_calls = []

        api_tool_calls = message.tool_calls or []
        for tc in api_tool_calls:
            if not (function_call := tc.function):
                continue
            try:
                args = json.loads(function_call.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            tool_calls.append(
                ParsedToolCall(
                    tool_name=function_call.name or "",
                    raw_args=args,
                    call_id=tc.id or "",
                )
            )

        return ParsedMessage(tool_calls=tool_calls)

    def resolve_tool_calls(
        self, parsed: ParsedMessage, tool_manager: ToolManager
    ) -> ResolvedMessage:
        resolved_calls = []
        failed_calls = []

        active_tools = tool_manager.available_tools

        for parsed_call in parsed.tool_calls:
            tool_class = active_tools.get(parsed_call.tool_name)
            if not tool_class:
                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=f"Unknown tool '{parsed_call.tool_name}'",
                    )
                )
                continue

            args_model, _ = tool_class._get_tool_args_results()
            try:
                validated_args = args_model.model_validate(parsed_call.raw_args)
                resolved_calls.append(
                    ResolvedToolCall(
                        tool_name=parsed_call.tool_name,
                        tool_class=tool_class,
                        validated_args=validated_args,
                        call_id=parsed_call.call_id,
                    )
                )
            except ValidationError as e:
                failed_calls.append(
                    FailedToolCall(
                        tool_name=parsed_call.tool_name,
                        call_id=parsed_call.call_id,
                        error=f"Invalid arguments: {e}",
                    )
                )

        return ResolvedMessage(tool_calls=resolved_calls, failed_calls=failed_calls)

    def create_tool_response_message(
        self, tool_call: ResolvedToolCall, result_text: str
    ) -> LLMMessage:
        return LLMMessage(
            role=Role.tool,
            tool_call_id=tool_call.call_id,
            name=tool_call.tool_name,
            content=result_text,
        )

    def create_failed_tool_response_message(
        self, failed: FailedToolCall, error_content: str
    ) -> LLMMessage:
        return LLMMessage(
            role=Role.tool,
            tool_call_id=failed.call_id,
            name=failed.tool_name,
            content=error_content,
        )
