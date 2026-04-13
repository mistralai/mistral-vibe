from __future__ import annotations

from enum import StrEnum
from http import HTTPStatus

import httpx


class ErrorCode(StrEnum):
    TEMPORAL_CONNECTION_ERROR = "temporal_connection_error"
    GET_EVENTS_STREAM_ERROR = "get_events_stream_error"
    POST_EXECUTIONS_SIGNALS_ERROR = "post_executions_signals_error"
    POST_EXECUTIONS_UPDATES_ERROR = "post_executions_updates_error"
    GET_EXECUTIONS_ERROR = "get_executions_error"


class WorkflowsException(Exception):
    def __init__(
        self,
        message: str,
        status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR,
        code: ErrorCode = ErrorCode.TEMPORAL_CONNECTION_ERROR,
    ) -> None:
        self.status = status
        self.message = message
        self.code = code

    def __str__(self) -> str:
        return f"{self.message} (code={self.code}, status={self.status.value})"

    @classmethod
    def from_api_client_error(
        cls,
        exc: Exception,
        message: str = "HTTP request failed",
        code: ErrorCode = ErrorCode.TEMPORAL_CONNECTION_ERROR,
    ) -> WorkflowsException:
        status = HTTPStatus.INTERNAL_SERVER_ERROR
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                status = HTTPStatus(exc.response.status_code)
            except ValueError:
                pass
        if isinstance(exc, httpx.ConnectError | httpx.TimeoutException):
            status = HTTPStatus.BAD_GATEWAY
        return cls(message=f"{message}: {exc}", code=code, status=status)
