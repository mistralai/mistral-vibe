"""Structured ACP error classes for the Vibe agent.

Error codes follow JSON-RPC 2.0 (https://www.jsonrpc.org/specification#error_object)
and ACP error handling (https://agentclientprotocol.com/protocol/overview#error-handling):

  -32700            Parse error (JSON-RPC standard)
  -32600            Invalid request (JSON-RPC standard)
  -32601            Method not found (JSON-RPC standard)
  -32602            Invalid params (JSON-RPC standard)
  -32603            Internal error (JSON-RPC standard)
  -32000 to -32099  Server errors (JSON-RPC implementation-defined)
  -31xxx            Application errors (Vibe-specific, outside reserved range)
"""

from __future__ import annotations

from typing import Any

from acp import RequestError

from vibe.core.config import MissingAPIKeyError
from vibe.core.types import (
    ContextTooLongError as CoreContextTooLongError,
    RateLimitError as CoreRateLimitError,
)

# JSON-RPC 2.0 standard codes
UNAUTHENTICATED = -32000
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Vibe application codes (outside JSON-RPC reserved range)
RATE_LIMITED = -31001
CONFIGURATION_ERROR = -31002
CONVERSATION_LIMIT = -31003
CONTEXT_TOO_LONG = -31004


class VibeRequestError(RequestError):
    code: int

    def __init__(self, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(self.code, message, data)


class UnauthenticatedError(VibeRequestError):
    code = UNAUTHENTICATED

    def __init__(self, detail: str) -> None:
        super().__init__(message=detail)

    @classmethod
    def from_missing_api_key(cls, exc: MissingAPIKeyError) -> UnauthenticatedError:
        return cls(f"Missing API key for {exc.provider_name} provider.")


class NotImplementedMethodError(VibeRequestError):
    code = METHOD_NOT_FOUND

    def __init__(self, method: str) -> None:
        super().__init__(
            message=f"Method not implemented: {method}", data={"method": method}
        )


class InvalidRequestError(VibeRequestError):
    code = INVALID_PARAMS

    def __init__(self, detail: str) -> None:
        super().__init__(message=detail)


class SessionNotFoundError(VibeRequestError):
    code = INVALID_PARAMS

    def __init__(self, session_id: str) -> None:
        super().__init__(
            message=f"Session not found: {session_id}", data={"session_id": session_id}
        )


class SessionLoadError(VibeRequestError):
    code = INVALID_PARAMS

    def __init__(self, session_id: str, detail: str) -> None:
        super().__init__(
            message=f"Failed to load session {session_id}: {detail}",
            data={"session_id": session_id},
        )


class RateLimitError(VibeRequestError):
    code = RATE_LIMITED

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(
            message=f"Rate limit exceeded for {provider} (model: {model}).",
            data={"provider": provider, "model": model},
        )

    @classmethod
    def from_core(cls, exc: CoreRateLimitError) -> RateLimitError:
        return cls(exc.provider, exc.model)


class ContextTooLongError(VibeRequestError):
    code = CONTEXT_TOO_LONG

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(
            message=f"Context too long for {provider} (model: {model}). "
            "Use /rewind to undo recent actions, then /compact to summarize.",
            data={"provider": provider, "model": model},
        )

    @classmethod
    def from_core(cls, exc: CoreContextTooLongError) -> ContextTooLongError:
        return cls(exc.provider, exc.model)


class ConfigurationError(VibeRequestError):
    code = CONFIGURATION_ERROR

    def __init__(self, detail: str) -> None:
        super().__init__(message=detail)


class ConversationLimitError(VibeRequestError):
    code = CONVERSATION_LIMIT

    def __init__(self, detail: str) -> None:
        super().__init__(message=detail)


class InternalError(VibeRequestError):
    code = INTERNAL_ERROR

    def __init__(self, detail: str) -> None:
        super().__init__(message=detail or "Internal error")
