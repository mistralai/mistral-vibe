"""OpenAI-compatible chat/completions adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from mistralai_vibe_local_harness.vibe.adapters.generic._base import (
    APIAdapter,
    PreparedRequest,
    build_chat_payload,
    finalize_chat_request,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._image import to_data_uri
from mistralai_vibe_local_harness.vibe.adapters.generic._model import (
    REASONING_BLOCK_TYPES,
    AvailableTool,
    FunctionCall,
    ImageAttachment,
    LLMChunk,
    LLMMessage,
    Role,
    StopInfo,
    StrToolChoice,
    ToolCall,
)
from mistralai_vibe_local_harness.vibe.adapters.generic._provider import ProviderView
from mistralai_vibe_local_harness.vibe.completion_profile import (
    ReasoningSetting,
    ReasoningSupport,
)

TOOL_RESULT_IMAGE_PLACEHOLDER = "[Image]"

# LiteLLM signs the thinking of models without signed reasoning with this
# placeholder. Such a block repeats `reasoning_content`, and Anthropic models
# reject its signature.
_PLACEHOLDER_SIGNATURE = "mistral"


def _tool_result_text(msg: LLMMessage) -> str:
    """A tool result's content as text, for a provider without tool-result images.

    Each image becomes `[Image]` after the result's text, as for a model
    without image input, and the turn goes on. Resources are text already.
    """
    parts = [msg.content] if msg.content else []
    parts.extend(TOOL_RESULT_IMAGE_PLACEHOLDER for _ in msg.images or ())
    return "\n\n".join(parts)


def _tool_result_content(
    msg: LLMMessage, provider: ProviderView
) -> str | list[dict[str, Any]]:
    """A tool result's content: its text then its images, as content parts.

    The images stay in the tool message, never in a user message: content a
    tool returns must not reach the model with the user's authority. Unless the
    provider sets `tool_result_images` (the Chat Completions spec allows only
    text in a tool message), each image becomes `[Image]`.
    """
    if not msg.images or not provider.tool_result_images:
        return _tool_result_text(msg)
    return _text_and_image_parts(msg.content, msg.images)


def _text_and_image_parts(
    text: str | None, images: Sequence[ImageAttachment]
) -> list[dict[str, Any]]:
    """Chat Completions content parts: ``text``, when there is some, then ``images``."""
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    parts.extend(
        {"type": "image_url", "image_url": {"url": to_data_uri(att)}} for att in images
    )
    return parts


def _replays_thinking_blocks(model_name: str) -> bool:
    """Whether the model reads signed `thinking_blocks` on assistant messages.

    Only Anthropic models can use them, and some OpenAI-compatible APIs reject
    unknown message fields, so no other model is sent the field.
    """
    return "claude" in model_name.rsplit("/", 1)[-1].lower()


def _signed_thinking_blocks(
    fragments: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """The complete, replayable thinking blocks of a reply's `thinking_blocks`.

    Anthropic models through LiteLLM-style gateways stream a thinking block as
    text fragments followed by a fragment carrying its `signature`, which closes
    the block. A reply that is not streamed carries complete blocks. Thinking
    without a real signature cannot be sent back and is dropped: its text stays
    in `reasoning_content`.
    """
    blocks: list[dict[str, Any]] = []
    text = ""
    for fragment in fragments:
        match fragment.get("type"):
            case "redacted_thinking" if fragment.get("data"):
                blocks.append({"type": "redacted_thinking", "data": fragment["data"]})
            case "thinking":
                text += fragment.get("thinking") or ""
                signature = fragment.get("signature")
                if signature and signature != _PLACEHOLDER_SIGNATURE:
                    blocks.append({
                        "type": "thinking",
                        "thinking": text,
                        "signature": signature,
                    })
                    text = ""
    return blocks


class OpenAIAdapter(APIAdapter):
    endpoint: ClassVar[str] = "/chat/completions"

    def _reasoning_to_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and "reasoning_content" in msg_dict:
            msg_dict[field_name] = msg_dict.pop("reasoning_content")
        return msg_dict

    def _reasoning_from_api(
        self, msg_dict: dict[str, Any], field_name: str
    ) -> dict[str, Any]:
        if field_name != "reasoning_content" and field_name in msg_dict:
            msg_dict["reasoning_content"] = msg_dict.pop(field_name)
        if thinking_blocks := msg_dict.pop("thinking_blocks", None):
            msg_dict["reasoning_payloads"] = thinking_blocks
        return msg_dict

    def _with_images(
        self, msg_dict: dict[str, Any], source: LLMMessage, provider: ProviderView
    ) -> dict[str, Any]:
        """Put the images of a user message or a tool result into its content."""
        if not source.images:
            return msg_dict
        match source.role:
            case Role.user:
                msg_dict["content"] = _text_and_image_parts(
                    source.content, source.images
                )
            case Role.tool:
                msg_dict["content"] = _tool_result_content(source, provider)
            case _:
                pass
        return msg_dict

    def _convert_message(
        self, msg: LLMMessage, provider: ProviderView, *, replay_thinking: bool
    ) -> dict[str, Any]:
        msg_dict = self._with_images(
            self._reasoning_to_api(
                msg.model_dump(
                    exclude_none=True,
                    exclude={"reasoning_payloads": True, "images": True},
                ),
                provider.reasoning_field_name,
            ),
            msg,
            provider,
        )
        # Anthropic models accept earlier thinking back only as signed blocks.
        if (
            replay_thinking
            and msg.role == Role.assistant
            and (
                thinking_blocks := [
                    block
                    for block in msg.reasoning_payloads or []
                    if block.get("type") in REASONING_BLOCK_TYPES
                ]
            )
        ):
            msg_dict["thinking_blocks"] = thinking_blocks
        return msg_dict

    def _convert_messages(
        self, messages: Sequence[LLMMessage], provider: ProviderView, model_name: str
    ) -> list[dict[str, Any]]:
        replay_thinking = _replays_thinking_blocks(model_name)
        return [
            self._convert_message(msg, provider, replay_thinking=replay_thinking)
            for msg in messages
        ]

    def prepare_request(  # noqa: PLR0913 - one keyword per request setting
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderView,
        api_key: str | None = None,
        thinking: ReasoningSetting | None = None,
        top_p: float | None = None,
        reasoning_support: ReasoningSupport | None = None,
    ) -> PreparedRequest:
        converted_messages = self._convert_messages(messages, provider, model_name)

        payload = build_chat_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
            top_p=top_p,
        )

        stream_options: dict[str, Any] = {"include_usage": True}
        if provider.name == "mistral":
            stream_options["stream_tool_calls"] = True

        return finalize_chat_request(
            payload=payload,
            enable_streaming=enable_streaming,
            stream_options=stream_options,
            api_key=api_key,
            endpoint=self.endpoint,
        )

    def _parse_message(
        self, data: dict[str, Any], field_name: str
    ) -> LLMMessage | None:
        if data.get("choices"):
            choice = data["choices"][0]
            if "message" in choice:
                msg_dict = self._reasoning_from_api(choice["message"], field_name)
                return LLMMessage.model_validate(msg_dict)
            if "delta" in choice:
                msg_dict = self._reasoning_from_api(choice["delta"], field_name)
                if msg_dict.get("role") is None:
                    msg_dict["role"] = Role.assistant
                return LLMMessage.model_validate(msg_dict)
            raise ValueError("Invalid response data: missing message or delta")

        if "message" in data:
            msg_dict = self._reasoning_from_api(data["message"], field_name)
            return LLMMessage.model_validate(msg_dict)
        if "delta" in data:
            msg_dict = self._reasoning_from_api(data["delta"], field_name)
            if msg_dict.get("role") is None:
                msg_dict["role"] = Role.assistant
            return LLMMessage.model_validate(msg_dict)

        return None

    def parse_response(self, data: dict[str, Any], provider: ProviderView) -> LLMChunk:
        message = self._parse_message(data, provider.reasoning_field_name)
        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        stop = (
            StopInfo(reason=str(finish_reason)) if finish_reason is not None else None
        )

        return LLMChunk(message=message, stop=stop)

    def finish_message(self, message: LLMMessage) -> LLMMessage:
        # A streamed thinking block is complete only once its signature arrives.
        return message.model_copy(
            update={
                "reasoning_payloads": _signed_thinking_blocks(
                    message.reasoning_payloads or []
                )
                or None
            }
        )


class ReasoningAdapter(APIAdapter):
    """OpenAI-compatible adapter for providers that emit structured thinking blocks."""

    endpoint: ClassVar[str] = "/chat/completions"

    def _convert_message(
        self, msg: LLMMessage, provider: ProviderView
    ) -> dict[str, Any]:
        match msg.role:
            case Role.system:
                return {"role": "system", "content": msg.content or ""}
            case Role.user:
                if msg.images:
                    return {
                        "role": "user",
                        "content": _text_and_image_parts(msg.content, msg.images),
                    }
                return {"role": "user", "content": msg.content or ""}
            case Role.assistant:
                return self._convert_assistant_message(msg)
            case Role.tool:
                result: dict[str, Any] = {
                    "role": "tool",
                    "content": _tool_result_content(msg, provider),
                    "tool_call_id": msg.tool_call_id,
                }
                if msg.name:
                    result["name"] = msg.name
                return result

    def _convert_assistant_message(self, msg: LLMMessage) -> dict[str, Any]:
        result: dict[str, Any] = {"role": "assistant"}

        if msg.reasoning_content:
            content: list[dict[str, Any]] = [
                {
                    "type": "thinking",
                    "thinking": [{"type": "text", "text": msg.reasoning_content}],
                }
            ]
            if msg.content:
                content.append({"type": "text", "text": msg.content})
            result["content"] = content
        else:
            result["content"] = msg.content or ""

        if msg.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name or "",
                        "arguments": tc.function.arguments or "",
                    },
                    **({"index": tc.index} if tc.index is not None else {}),
                }
                for tc in msg.tool_calls
            ]

        return result

    def prepare_request(  # noqa: PLR0913 - one keyword per request setting
        self,
        *,
        model_name: str,
        messages: Sequence[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        enable_streaming: bool,
        provider: ProviderView,
        api_key: str | None = None,
        thinking: ReasoningSetting | None = None,
        top_p: float | None = None,
        reasoning_support: ReasoningSupport | None = None,
    ) -> PreparedRequest:
        converted_messages = [self._convert_message(msg, provider) for msg in messages]

        payload = build_chat_payload(
            model_name=model_name,
            messages=converted_messages,
            temperature=temperature,
            tools=tools,
            max_tokens=max_tokens,
            tool_choice=tool_choice,
            thinking=thinking,
            top_p=top_p,
        )

        return finalize_chat_request(
            payload=payload,
            enable_streaming=enable_streaming,
            stream_options={"include_usage": True, "stream_tool_calls": True},
            api_key=api_key,
            endpoint=self.endpoint,
        )

    @staticmethod
    def _parse_content_blocks(
        content: str | list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        if isinstance(content, str):
            return content or None, None

        text_parts: list[str] = []
        thinking_parts: list[str] = []

        for block in content:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text", ""))
            elif block_type == "thinking":
                for inner in block.get("thinking", []):
                    if isinstance(inner, dict) and inner.get("type") == "text":
                        thinking_parts.append(inner.get("text", ""))
                    elif isinstance(inner, str):
                        thinking_parts.append(inner)

        return ("".join(text_parts) or None, "".join(thinking_parts) or None)

    @staticmethod
    def _parse_tool_calls(
        tool_calls: list[dict[str, Any]] | None,
    ) -> list[ToolCall] | None:
        if not tool_calls:
            return None
        return [
            ToolCall(
                id=tc.get("id"),
                index=tc.get("index"),
                function=FunctionCall(
                    name=tc.get("function", {}).get("name"),
                    arguments=tc.get("function", {}).get("arguments", ""),
                ),
            )
            for tc in tool_calls
        ]

    def _parse_message_dict(self, msg_dict: dict[str, Any]) -> LLMMessage:
        content = msg_dict.get("content")
        text_content: str | None = None
        reasoning_content: str | None = None

        if content is not None:
            text_content, reasoning_content = self._parse_content_blocks(content)

        return LLMMessage(
            role=Role.assistant,
            content=text_content,
            reasoning_content=reasoning_content,
            tool_calls=self._parse_tool_calls(msg_dict.get("tool_calls")),
        )

    def parse_response(self, data: dict[str, Any], provider: ProviderView) -> LLMChunk:
        message: LLMMessage | None = None

        if data.get("choices"):
            choice = data["choices"][0]
            if "message" in choice:
                message = self._parse_message_dict(choice["message"])
            elif "delta" in choice:
                message = self._parse_message_dict(choice["delta"])

        if message is None:
            message = LLMMessage(role=Role.assistant, content="")

        choices = data.get("choices") or []
        finish_reason = choices[0].get("finish_reason") if choices else None
        stop = (
            StopInfo(reason=str(finish_reason)) if finish_reason is not None else None
        )

        return LLMChunk(message=message, stop=stop)


__all__ = ["OpenAIAdapter", "ReasoningAdapter"]
