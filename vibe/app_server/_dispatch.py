from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydantic import JsonValue

from vibe.app_server._model import ProtocolModel
from vibe.app_server.protocol import ProtocolErrorCode


@dataclass(frozen=True, slots=True)
class DispatchResult:
    response: ProtocolModel
    after_response: Callable[[], None] | None = None
    runtime_updated: bool = False
    session_attached: bool = False


class RequestFailure(Exception):
    def __init__(
        self, code: ProtocolErrorCode, message: str, data: JsonValue = None
    ) -> None:
        self.code = code
        self.data = data
        super().__init__(message)


def method_not_found(method: str) -> RequestFailure:
    return RequestFailure(
        ProtocolErrorCode.METHOD_NOT_FOUND, f"Method not found: {method}"
    )
