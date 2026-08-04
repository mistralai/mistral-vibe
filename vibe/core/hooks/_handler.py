from __future__ import annotations

from abc import ABC, abstractmethod
import json
import logging
from typing import NamedTuple, TypedDict

from pydantic import ValidationError

from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.models import (
    HookEvent,
    HookExecutionResult,
    HookInvocation,
    HookStructuredResponse,
    HookTextReplacement,
    HookToolDenial,
    HookToolInputRewrite,
    HookUserMessage,
)

logger = logging.getLogger(__name__)


_MAX_RETRIES = 3


_HookYield = (
    HookEvent
    | HookUserMessage
    | HookToolDenial
    | HookToolInputRewrite
    | HookTextReplacement
)


class HookExternalAttrs(TypedDict, total=False):
    tool_name: str
    tool_call_id: str


class _HookAction(NamedTuple):
    events: list[_HookYield]
    # The invocation the next hook in the chain receives; ``None`` keeps
    # the current one.
    next_invocation: HookInvocation | None
    should_break: bool


class HookRetryState:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    def reset(self) -> None:
        self._counts.clear()

    def remaining_retries(self, hook_name: str) -> int:
        return _MAX_RETRIES - self._counts.get(hook_name, 0)

    def track_retry(self, hook_name: str) -> None:
        self._counts[hook_name] = self._counts.get(hook_name, 0) + 1

    def track_no_retry(self, hook_name: str) -> None:
        self._counts.pop(hook_name, None)

    def should_retry(self, hook_name: str) -> bool:
        return self._counts.get(hook_name, 0) < _MAX_RETRIES


class HookOutputError(ValueError):
    """Hook stdout was non-empty but did not match the structured-response
    spec. The manager treats this as a hook failure (warning by default,
    deny / clear under ``strict``).
    """


def _parse_structured_response(stdout: str) -> HookStructuredResponse | None:
    """Return the parsed response, or ``None`` for an empty stdout.

    Raises :class:`HookOutputError` for any other non-conforming output.
    """
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise HookOutputError(
            f"stdout was not valid JSON: {e.msg} at line {e.lineno} col {e.colno}"
        ) from e
    if not isinstance(parsed, dict):
        raise HookOutputError(
            f"stdout was a JSON {type(parsed).__name__}, expected an object"
        )
    try:
        return HookStructuredResponse.model_validate(parsed)
    except ValidationError as e:
        raise HookOutputError(
            f"stdout JSON did not match the hook response schema: {e}"
        ) from e


def _failure_reason(result: HookExecutionResult) -> str:
    # Prefer stderr: stdout is reserved for the JSON response and is
    # likely empty / garbage when the hook crashed.
    if result.timed_out or result.exit_code is None:
        return "timed out"
    return result.stderr or result.stdout or f"exited with code {result.exit_code}"


def _append_text(base: str, addition: str) -> str:
    if not base:
        return addition
    return f"{base}\n{addition}"


class HookHandler(ABC):
    """Per-type hook semantics. Stateless singleton; per-run state is
    passed in through method parameters.
    """

    @abstractmethod
    def matches(self, hook: HookConfig, invocation: HookInvocation) -> bool: ...

    def external_attributes(self, invocation: HookInvocation) -> HookExternalAttrs:
        return {}

    def on_structured(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        if response.decision == "deny":
            return self._on_deny(hook, invocation, response, retry_state)
        return self._on_allow(hook, invocation, response, retry_state)

    @abstractmethod
    def _on_deny(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        """Read the deny reason as ``response.reason or ""`` — empty is a
        valid explicit denial.
        """

    @abstractmethod
    def _on_allow(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction: ...

    @abstractmethod
    def on_passthrough(self, hook: HookConfig, retry_state: HookRetryState) -> None:
        """Side effect of a no-op outcome (empty stdout or non-strict
        failure).
        """

    def on_strict_failure(
        self, hook: HookConfig, invocation: HookInvocation, reason: str
    ) -> _HookAction | None:
        """Return an escalation action, or ``None`` to fall through to a
        plain warning.
        """
        return None
