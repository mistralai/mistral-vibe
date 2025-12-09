from __future__ import annotations

from fnmatch import fnmatch
from functools import lru_cache
import json
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.tools.base import BaseTool
from vibe.core.types import (
    AvailableFunction,
    AvailableTool,
    LLMMessage,
    Role,
    StrToolChoice,
)

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig
    from vibe.core.tools.manager import ToolManager


def _is_regex_hint(pattern: str) -> bool:
    """Heuristically detect whether a pattern looks like a regex.

    - Explicit regex: starts with 're:'
    - Heuristic regex: contains common regex metachars or '.*'
    """
    if pattern.startswith("re:"):
        return True
    return bool(re.search(r"[().+|^$]", pattern) or ".*" in pattern)


@lru_cache(maxsize=256)
def _compile_icase(expr: str) -> re.Pattern | None:
    try:
        return re.compile(expr, re.IGNORECASE)
    except re.error:
        return None


def _regex_match_icase(expr: str, s: str) -> bool:
    rx = _compile_icase(expr)
    return rx is not None and rx.fullmatch(s) is not None


def _name_matches(name: str, patterns: list[str]) -> bool:
    """Check if a tool name matches any of the provided patterns.

    Supports three forms (case-insensitive):
    - Exact names (no wildcards/regex tokens)
    - Glob wildcards using fnmatch (e.g., 'serena_*')
    - Regex when prefixed with 're:'
      or when the pattern looks regex-y (e.g., 'serena.*')
    """
    n = name.lower()
    for raw in patterns:
        if not (p := (raw or "").strip()):
            continue

        match p:
            case _ if p.startswith("re:"):
                if _regex_match_icase(p.removeprefix("re:"), name):
                    return True
            case _ if _is_regex_hint(p):
                if _regex_match_icase(p, name):
                    return True
            case _:
                if fnmatch(n, p.lower()):
                    return True

    return False


def get_active_tool_classes(
    tool_manager: ToolManager, config: VibeConfig
) -> list[type[BaseTool]]:
    """Returns a list of active tool classes based on the configuration.

    Args:
        tool_manager: ToolManager instance with discovered tools
        config: VibeConfig with enabled_tools/disabled_tools settings
    """
    all_tools = list(tool_manager.available_tools().values())

    if config.enabled_tools:
        return [
            tool_class
            for tool_class in all_tools
            if _name_matches(tool_class.get_name(), config.enabled_tools)
        ]

    if config.disabled_tools:
        return [
            tool_class
            for tool_class in all_tools
            if not _name_matches(tool_class.get_name(), config.disabled_tools)
        ]

    return all_tools


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

    def get_available_tools(
        self, tool_manager: ToolManager, config: VibeConfig
    ) -> list[AvailableTool]:
        active_tools = get_active_tool_classes(tool_manager, config)

        return [
            AvailableTool(
                function=AvailableFunction(
                    name=tool_class.get_name(),
                    description=tool_class.description,
                    parameters=tool_class.get_parameters(),
                )
            )
            for tool_class in active_tools
        ]

    def get_tool_choice(self) -> StrToolChoice | AvailableTool:
        return "auto"

    def process_api_response_message(self, message: Any) -> LLMMessage:
        clean_message = {"role": message.role, "content": message.content}

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
        self, parsed: ParsedMessage, tool_manager: ToolManager, config: VibeConfig
    ) -> ResolvedMessage:
        resolved_calls = []
        failed_calls = []

        active_tools = {
            tool_class.get_name(): tool_class
            for tool_class in get_active_tool_classes(tool_manager, config)
        }

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
