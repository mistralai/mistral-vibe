from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, TypedDict, cast

from pydantic import TypeAdapter

from vibe.core.llm.backend.base import APIAdapter, PreparedRequest
from vibe.core.llm.message_utils import merge_consecutive_user_messages
from vibe.core.types import (
    AvailableTool,
    FunctionCall,
    LLMChunk,
    LLMMessage,
    LLMUsage,
    Role,
    StrToolChoice,
    ToolCall,
)

if TYPE_CHECKING:
    from vibe.core.config import ProviderConfig

logger = logging.getLogger(__name__)

_EMPTY_USAGE = LLMUsage(prompt_tokens=0, completion_tokens=0)


class _ResponsesUsageData(TypedDict, total=False):
    input_tokens: int
    output_tokens: int


class _ResponsesFunctionCallItem(TypedDict, total=False):
    type: str
    id: str
    call_id: str
    name: str
    arguments: str


class _ResponsesContentBlock(TypedDict, total=False):
    type: str
    text: str


class _ResponsesSummaryBlock(TypedDict, total=False):
    type: str
    text: str


class _ResponsesMessageItem(TypedDict, total=False):
    type: str
    id: str
    role: str
    phase: str
    content: list[_ResponsesContentBlock]


class _ResponsesReasoningItem(TypedDict, total=False):
    type: str
    encrypted_content: str
    summary: list[_ResponsesSummaryBlock]


class _ResponsesObject(TypedDict, total=False):
    usage: _ResponsesUsageData | None
    output: list[dict[str, Any]]


class _ResponsesErrorData(TypedDict, total=False):
    type: str
    message: str


class _ResponsesStreamEvent(TypedDict, total=False):
    type: str
    output_index: int
    delta: str
    call_id: str
    name: str
    arguments: str
    item: dict[str, Any]
    response: _ResponsesObject
    error: _ResponsesErrorData


_RESPONSES_OBJECT_ADAPTER = TypeAdapter(_ResponsesObject)
_RESPONSES_STREAM_EVENT_ADAPTER = TypeAdapter(_ResponsesStreamEvent)
_RESPONSES_FUNCTION_CALL_ITEM_ADAPTER = TypeAdapter(_ResponsesFunctionCallItem)
_RESPONSES_MESSAGE_ITEM_ADAPTER = TypeAdapter(_ResponsesMessageItem)
_RESPONSES_REASONING_ITEM_ADAPTER = TypeAdapter(_ResponsesReasoningItem)
_RESPONSES_ERROR_DATA_ADAPTER = TypeAdapter(_ResponsesErrorData)


@dataclass(slots=True)
class _ResponsesToolCallState:
    call_id: str | None = None
    name: str | None = None
    arguments: str = ""
    name_emitted: bool = False
    arguments_emitted: bool = False


class _OpenAIResponsesStreamParser:
    def __init__(self) -> None:
        self._commentary_indices: set[int] = set()
        self._ignored_event_types: set[str] = set()
        self._tool_call_states: dict[int, _ResponsesToolCallState] = {}

    def reset(self) -> None:
        self._commentary_indices.clear()
        self._ignored_event_types.clear()
        self._tool_call_states.clear()

    def parse(self, data: _ResponsesStreamEvent) -> LLMChunk:
        handler = self._EVENT_HANDLERS.get(data.get("type", ""))
        if handler is not None:
            return handler(self, data)
        return self._on_unknown_event(data)

    @staticmethod
    def _is_commentary_message(item: dict[str, Any]) -> bool:
        return item.get("type") == "message" and item.get("phase") == "commentary"

    @staticmethod
    def _usage_from_response(usage_data: _ResponsesUsageData | None) -> LLMUsage:
        usage = usage_data or {}
        return LLMUsage(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
        )

    @staticmethod
    def _reasoning_state_from_output(output: list[dict[str, Any]]) -> list[str] | None:
        reasoning_state: list[str] = []
        for item in output:
            if item.get("type") != "reasoning":
                continue
            reasoning_item = _RESPONSES_REASONING_ITEM_ADAPTER.validate_python(item)
            encrypted_content = reasoning_item.get("encrypted_content")
            if encrypted_content:
                reasoning_state.append(encrypted_content)
        return reasoning_state or None

    @staticmethod
    def _tool_call_from_item(
        item: _ResponsesFunctionCallItem, *, index: int | None = None
    ) -> ToolCall:
        item = _RESPONSES_FUNCTION_CALL_ITEM_ADAPTER.validate_python(item)
        return ToolCall(
            id=item.get("call_id") or item.get("id"),
            index=index,
            function=FunctionCall(
                name=item.get("name"), arguments=item.get("arguments", "")
            ),
        )

    @staticmethod
    def _empty_chunk() -> LLMChunk:
        return LLMChunk(
            message=LLMMessage(role=Role.assistant, content=""), usage=_EMPTY_USAGE
        )

    @staticmethod
    def _assistant_text_chunk(text: str) -> LLMChunk:
        return LLMChunk(
            message=LLMMessage(role=Role.assistant, content=text), usage=_EMPTY_USAGE
        )

    @staticmethod
    def _tool_call_chunk(
        call_id: str | None, name: str | None, arguments: str, index: int | None
    ) -> LLMChunk:
        if index is None:
            raise ValueError("Tool call chunk missing index")
        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content="",
                tool_calls=[
                    ToolCall(
                        id=call_id,
                        index=index,
                        function=FunctionCall(name=name, arguments=arguments),
                    )
                ],
            ),
            usage=_EMPTY_USAGE,
        )

    @staticmethod
    def _reasoning_chunk(reasoning_content: str) -> LLMChunk:
        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant, content="", reasoning_content=reasoning_content
            ),
            usage=_EMPTY_USAGE,
        )

    def _remember_tool_call_state(
        self,
        *,
        index: int,
        call_id: str | None,
        name: str | None,
        arguments: str | None,
        name_emitted: bool | None = None,
        arguments_emitted: bool | None = None,
    ) -> None:
        state = self._tool_call_states.setdefault(index, _ResponsesToolCallState())
        if call_id:
            state.call_id = call_id
        if name:
            state.name = name
        if arguments is not None:
            state.arguments = arguments
        if name_emitted is not None:
            state.name_emitted = name_emitted
        if arguments_emitted is not None:
            state.arguments_emitted = arguments_emitted

    def _finalize_tool_call(
        self,
        *,
        index: int | None,
        call_id: str | None,
        name: str | None,
        arguments: str | None,
    ) -> LLMChunk:
        if index is None:
            raise ValueError("Tool call chunk missing index")

        state = self._tool_call_states.get(index, _ResponsesToolCallState())
        resolved_call_id = call_id or state.call_id
        resolved_name = name or state.name
        previous_arguments = state.arguments
        final_arguments = arguments if arguments is not None else previous_arguments
        if (
            previous_arguments
            and final_arguments
            and not final_arguments.startswith(previous_arguments)
        ):
            logger.warning(
                "OpenAI Responses tool call arguments mismatch; using full final arguments from done event. previous=%r current=%r",
                previous_arguments,
                final_arguments,
            )

        should_emit_name = bool(resolved_name and not state.name_emitted)
        should_emit_arguments = bool(final_arguments) and not state.arguments_emitted

        self._remember_tool_call_state(
            index=index,
            call_id=resolved_call_id,
            name=resolved_name,
            arguments=final_arguments,
            name_emitted=state.name_emitted or should_emit_name,
            arguments_emitted=state.arguments_emitted or should_emit_arguments,
        )

        if not should_emit_name and not should_emit_arguments:
            return self._empty_chunk()

        return self._tool_call_chunk(
            call_id=resolved_call_id,
            name=resolved_name,
            arguments=final_arguments if should_emit_arguments else "",
            index=index,
        )

    def _on_response_created(self, _data: _ResponsesStreamEvent) -> LLMChunk:
        self.reset()
        return self._empty_chunk()

    def _on_text_delta(self, data: _ResponsesStreamEvent) -> LLMChunk:
        delta = data.get("delta", "")
        if data.get("output_index", 0) not in self._commentary_indices:
            return self._assistant_text_chunk(delta)
        return self._reasoning_chunk(delta)

    def _on_reasoning_delta(self, data: _ResponsesStreamEvent) -> LLMChunk:
        return self._reasoning_chunk(data.get("delta", ""))

    def _on_tool_call_delta(self, data: _ResponsesStreamEvent) -> LLMChunk:
        delta = data.get("delta", "")
        if not delta and not data.get("name") and not data.get("call_id"):
            return self._empty_chunk()

        index = data.get("output_index")
        if index is None:
            raise ValueError("Tool call chunk missing index")

        state = self._tool_call_states.get(index, _ResponsesToolCallState())
        self._remember_tool_call_state(
            index=index,
            call_id=data.get("call_id"),
            name=data.get("name"),
            arguments=state.arguments + delta,
            name_emitted=state.name_emitted,
            arguments_emitted=state.arguments_emitted,
        )
        return self._empty_chunk()

    def _on_output_item_added(self, data: _ResponsesStreamEvent) -> LLMChunk:
        item = data.get("item") or {}
        match item.get("type"):
            case "message" if self._is_commentary_message(item):
                self._commentary_indices.add(data.get("output_index", 0))
            case "function_call":
                item = _RESPONSES_FUNCTION_CALL_ITEM_ADAPTER.validate_python(item)
                index = data.get("output_index")
                if index is not None:
                    self._remember_tool_call_state(
                        index=index,
                        call_id=item.get("call_id") or item.get("id"),
                        name=item.get("name"),
                        arguments=item.get("arguments", ""),
                        name_emitted=bool(item.get("name")),
                        arguments_emitted=False,
                    )
                tool_call = self._tool_call_from_item(
                    cast(_ResponsesFunctionCallItem, item), index=index
                )
                return self._tool_call_chunk(
                    call_id=tool_call.id,
                    name=tool_call.function.name,
                    arguments="",
                    index=tool_call.index,
                )
        return self._empty_chunk()

    def _on_tool_call_done(self, data: _ResponsesStreamEvent) -> LLMChunk:
        return self._finalize_tool_call(
            index=data.get("output_index"),
            call_id=data.get("call_id"),
            name=data.get("name"),
            arguments=data.get("arguments"),
        )

    def _on_output_item_done(self, data: _ResponsesStreamEvent) -> LLMChunk:
        item = data.get("item") or {}
        match item.get("type"):
            case "message" if self._is_commentary_message(item):
                self._commentary_indices.add(data.get("output_index", 0))
            case "function_call":
                item = _RESPONSES_FUNCTION_CALL_ITEM_ADAPTER.validate_python(item)
                return self._finalize_tool_call(
                    index=data.get("output_index"),
                    call_id=item.get("call_id") or item.get("id"),
                    name=item.get("name"),
                    arguments=item.get("arguments"),
                )
        return self._empty_chunk()

    def _on_response_terminal(self, data: _ResponsesStreamEvent) -> LLMChunk:
        response_obj = cast(_ResponsesObject, data.get("response") or {})
        self.reset()
        output = response_obj.get("output") or []
        return LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content="",
                reasoning_state=self._reasoning_state_from_output(output),
            ),
            usage=self._usage_from_response(response_obj.get("usage")),
        )

    def _on_error(self, data: _ResponsesStreamEvent) -> LLMChunk:
        self.reset()
        error = _RESPONSES_ERROR_DATA_ADAPTER.validate_python(data.get("error") or {})
        error_type = error.get("type", "unknown_error")
        error_message = error.get("message", "Unknown streaming error")
        raise RuntimeError(
            f"OpenAI Responses stream error ({error_type}): {error_message}"
        )

    def _on_unknown_event(self, data: _ResponsesStreamEvent) -> LLMChunk:
        if event_type := data.get("type"):
            if event_type not in self._ignored_event_types:
                logger.debug(
                    "Ignoring OpenAI Responses stream event type: %s", event_type
                )
                self._ignored_event_types.add(event_type)
        return self._empty_chunk()

    _EVENT_HANDLERS: ClassVar[
        dict[
            str,
            Callable[[_OpenAIResponsesStreamParser, _ResponsesStreamEvent], LLMChunk],
        ]
    ] = {
        "response.created": _on_response_created,
        "response.output_text.delta": _on_text_delta,
        "response.reasoning_summary_text.delta": _on_reasoning_delta,
        "response.summary_text.delta": _on_reasoning_delta,
        "response.function_call_arguments.delta": _on_tool_call_delta,
        "response.function_call_arguments.done": _on_tool_call_done,
        "response.output_item.added": _on_output_item_added,
        "response.output_item.done": _on_output_item_done,
        "response.completed": _on_response_terminal,
        "response.incomplete": _on_response_terminal,
        "error": _on_error,
    }


class OpenAIResponsesAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/responses"

    def __init__(self) -> None:
        self._stream_parser = _OpenAIResponsesStreamParser()

    @staticmethod
    def _is_temperature_supported(model_name: str) -> bool:
        supported_prefixes = ("gpt-4", "gpt-3.5")
        return model_name.startswith(supported_prefixes)

    @staticmethod
    def _map_reasoning_effort(thinking: str) -> str:
        if thinking == "off":
            return "none"
        if thinking == "max":
            return "xhigh"
        return thinking

    def _convert_messages(self, messages: Sequence[LLMMessage]) -> list[dict[str, Any]]:
        input_items: list[dict[str, Any]] = []

        for msg in messages:
            match msg.role:
                case Role.system:
                    input_items.append({"role": "system", "content": msg.content or ""})

                case Role.user:
                    input_items.append({"role": "user", "content": msg.content or ""})

                case Role.assistant:
                    for encrypted_content in msg.reasoning_state or []:
                        input_items.append({
                            "type": "reasoning",
                            "encrypted_content": encrypted_content,
                        })
                    input_items.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": msg.content or ""}],
                    })
                    for tc in msg.tool_calls or []:
                        input_items.append({
                            "type": "function_call",
                            "call_id": tc.id or "",
                            "name": tc.function.name or "",
                            "arguments": tc.function.arguments or "",
                        })

                case Role.tool:
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": msg.tool_call_id or "",
                        "output": msg.content or "",
                    })

                case _:
                    raise ValueError(f"Unsupported role: {msg.role}")

        return input_items

    def _convert_tool_for_responses(self, tool: AvailableTool) -> dict[str, Any]:
        return {
            "type": "function",
            "name": tool.function.name,
            "description": tool.function.description,
            "parameters": tool.function.parameters,
        }

    def build_payload(
        self,
        *,
        model_name: str,
        input_items: list[dict[str, Any]],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        thinking: str,
        enable_streaming: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model_name,
            "input": input_items,
            "store": False,
        }
        if self._is_temperature_supported(model_name):
            payload["temperature"] = temperature

        payload["reasoning"] = {"effort": self._map_reasoning_effort(thinking)}

        if tools:
            payload["tools"] = [
                self._convert_tool_for_responses(tool) for tool in tools
            ]

        if tools and tool_choice:
            if isinstance(tool_choice, str):
                payload["tool_choice"] = tool_choice
            else:
                payload["tool_choice"] = {
                    "type": "function",
                    "name": tool_choice.function.name,
                }

        if max_tokens is not None:
            payload["max_output_tokens"] = max_tokens

        if enable_streaming:
            payload["stream"] = True

        return payload

    def build_headers(self, api_key: str | None = None) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers

    def prepare_request(
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderConfig,
        api_key: str | None = None,
        thinking: str = "off",
    ) -> PreparedRequest:
        merged_messages = merge_consecutive_user_messages(messages)
        input_items = self._convert_messages(merged_messages)

        payload = self.build_payload(
            model_name=model_name,
            input_items=input_items,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
            enable_streaming=enable_streaming,
        )

        headers = self.build_headers(api_key)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        return PreparedRequest(self.endpoint, headers, body)

    def _parse_output_items(self, output: list[dict[str, Any]]) -> LLMMessage:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for index, item in enumerate(output):
            match item.get("type"):
                case "message":
                    msg = _RESPONSES_MESSAGE_ITEM_ADAPTER.validate_python(item)
                    item_text_parts: list[str] = []
                    item_reasoning_parts: list[str] = []
                    is_commentary = self._stream_parser._is_commentary_message(item)

                    for block in msg.get("content", []):
                        block_type = block.get("type")
                        if is_commentary and block_type in {
                            "output_text",
                            "summary_text",
                            "reasoning_summary_text",
                        }:
                            item_reasoning_parts.append(block.get("text", ""))
                            continue

                        if block_type == "output_text":
                            item_text_parts.append(block.get("text", ""))

                    text = "".join(item_text_parts)
                    reasoning_content = "".join(item_reasoning_parts)
                    if is_commentary:
                        if reasoning_content:
                            reasoning_parts.append(reasoning_content)
                        continue
                    if text:
                        text_parts.append(text)
                    if reasoning_content:
                        reasoning_parts.append(reasoning_content)

                case "reasoning":
                    item = _RESPONSES_REASONING_ITEM_ADAPTER.validate_python(item)
                    for summary in item.get("summary", []):
                        if summary.get("type") in {
                            "summary_text",
                            "reasoning_summary_text",
                        }:
                            reasoning_parts.append(summary.get("text", ""))

                case "function_call":
                    tool_calls.append(
                        self._stream_parser._tool_call_from_item(
                            cast(_ResponsesFunctionCallItem, item), index=index
                        )
                    )

        return LLMMessage(
            role=Role.assistant,
            content="".join(text_parts),
            reasoning_content="".join(reasoning_parts) or None,
            reasoning_state=self._stream_parser._reasoning_state_from_output(output),
            tool_calls=tool_calls or None,
        )

    def parse_response(
        self, data: dict[str, Any], provider: ProviderConfig
    ) -> LLMChunk:
        event_type = data.get("type", "")

        if "output" in data and not event_type:
            response_data = _RESPONSES_OBJECT_ADAPTER.validate_python(data)
            output = response_data.get("output")
            if output is None:
                raise ValueError("OpenAI Responses response missing output")
            return LLMChunk(
                message=self._parse_output_items(output),
                usage=self._stream_parser._usage_from_response(
                    response_data.get("usage")
                ),
            )

        return self._stream_parser.parse(
            _RESPONSES_STREAM_EVENT_ADAPTER.validate_python(data)
        )
