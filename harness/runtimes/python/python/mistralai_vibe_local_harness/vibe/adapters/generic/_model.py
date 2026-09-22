"""Lean provider-facing message model.

``LLMChunk.__add__`` and ``LLMMessage.__add__`` implement streaming
accumulation, so reducing a stream of chunks with ``+`` yields the final
assistant message.
"""

import copy
from collections import OrderedDict
from enum import StrEnum, auto
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

StrToolChoice = Literal["auto", "none", "any", "required"]


class AvailableFunction(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]


class AvailableTool(BaseModel):
    type: Literal["function"] = "function"
    function: AvailableFunction


class FunctionCall(BaseModel):
    name: str | None = None
    arguments: str | None = None


class ToolCall(BaseModel):
    id: str | None = None
    index: int | None = None
    function: FunctionCall = Field(default_factory=FunctionCall)
    type: Literal["function"] = "function"


def _content_before(v: Any) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts: list[str] = []
        for p in v:
            if isinstance(p, dict) and isinstance(p.get("text"), str):
                parts.append(p["text"])
            else:
                parts.append(str(p))
        return "\n".join(parts)
    return str(v)


Content = Annotated[str, BeforeValidator(_content_before)]


class Role(StrEnum):
    system = auto()
    user = auto()
    assistant = auto()
    tool = auto()


class FileImageSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["file"] = "file"
    path: Path


class InlineImageSource(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["inline"] = "inline"
    # Raw base64-encoded bytes (no `data:` prefix).
    data: str


class ImageAttachment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: Annotated[FileImageSource | InlineImageSource, Field(discriminator="kind")]
    alias: str = ""
    mime_type: str


class LLMMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Role
    content: Content | None = None
    images: list[ImageAttachment] | None = None
    reasoning_content: Content | None = None
    reasoning_payloads: list[dict[str, Any]] | None = None
    tool_calls: list[ToolCall] | None = None
    name: str | None = None
    tool_call_id: str | None = None

    def __add__(self, other: "LLMMessage") -> "LLMMessage":
        """Accumulate a streamed chunk. Careful: this is not commutative!"""
        if self.role != other.role:
            raise ValueError("Can't accumulate messages with different roles")
        if self.name != other.name:
            raise ValueError("Can't accumulate messages with different names")
        if self.tool_call_id != other.tool_call_id:
            raise ValueError("Can't accumulate messages with different tool_call_ids")

        content = (self.content or "") + (other.content or "")
        if not content:
            content = None

        reasoning_content = (self.reasoning_content or "") + (other.reasoning_content or "")
        if not reasoning_content:
            reasoning_content = None

        reasoning_payloads = [
            *(self.reasoning_payloads or []),
            *(other.reasoning_payloads or []),
        ] or None

        tool_calls_map = OrderedDict[int, ToolCall]()
        for tool_calls in [self.tool_calls or [], other.tool_calls or []]:
            for tc in tool_calls:
                if tc.index is None:
                    raise ValueError("Tool call chunk missing index")
                if tc.index not in tool_calls_map:
                    tool_calls_map[tc.index] = copy.deepcopy(tc)
                else:
                    existing_name = tool_calls_map[tc.index].function.name
                    new_name = tc.function.name
                    if existing_name and new_name and existing_name != new_name:
                        raise ValueError("Can't accumulate messages with different tool call names")
                    if new_name and not existing_name:
                        tool_calls_map[tc.index].function.name = new_name
                    new_args = (tool_calls_map[tc.index].function.arguments or "") + (
                        tc.function.arguments or ""
                    )
                    tool_calls_map[tc.index].function.arguments = new_args

        return LLMMessage(
            role=self.role,
            content=content,
            images=self.images if self.images is not None else other.images,
            reasoning_content=reasoning_content,
            reasoning_payloads=reasoning_payloads,
            tool_calls=list(tool_calls_map.values()) or None,
            name=self.name,
            tool_call_id=self.tool_call_id,
        )


class LLMUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Prompt tokens served from the provider cache; a subset of prompt_tokens.
    cached_tokens: int = 0

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
        )


class StopInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    reason: str | None = None
    category: str | None = None
    explanation: str | None = None


class LLMChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: LLMMessage
    usage: LLMUsage | None = None
    stop: StopInfo | None = None

    def __add__(self, other: "LLMChunk") -> "LLMChunk":
        if self.usage is None and other.usage is None:
            new_usage = None
        else:
            new_usage = (self.usage or LLMUsage()) + (other.usage or LLMUsage())
        return LLMChunk(
            message=self.message + other.message,
            usage=new_usage,
            stop=other.stop or self.stop,
        )


__all__ = [
    "AvailableFunction",
    "AvailableTool",
    "Content",
    "FileImageSource",
    "FunctionCall",
    "ImageAttachment",
    "InlineImageSource",
    "LLMChunk",
    "LLMMessage",
    "LLMUsage",
    "Role",
    "StopInfo",
    "StrToolChoice",
    "ToolCall",
]
