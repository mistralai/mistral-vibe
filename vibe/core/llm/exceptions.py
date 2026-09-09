from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.core.types import AvailableTool, LLMMessage, StrToolChoice
from vibe.utils.api_keys import ApiKeyOrigin

_CONTEXT_TOO_LONG_SUBSTRINGS = (
    "context too long",
    "maximum context length",
    "input too large",
    "couldn't fit with truncation",
    "prompt is too long",
    # orchestral_runtime returns these as 422
    "model_context_exceeded",
    "prompt_too_long",
)

_RESPONSE_TOO_LONG_SUBSTRINGS = ("max_tokens_exceeded", "finish_reason=length")

_INVALID_MODEL_SUBSTRINGS = ("invalid_model",)


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str | None = None


class PayloadSummary(BaseModel):
    model: str
    message_count: int
    approx_chars: int
    temperature: float
    has_tools: bool
    tool_choice: StrToolChoice | AvailableTool | None


@dataclass(frozen=True, slots=True)
class ModelCall:
    """The call a backend was making when it failed.

    All three builders need the same eight values; only the failure differs.
    """

    provider: str
    endpoint: str
    model: str
    messages: Sequence[LLMMessage]
    temperature: float
    has_tools: bool
    tool_choice: StrToolChoice | AvailableTool | None
    # Where the credential came from, for the one error where that is the
    # actionable part. ``None`` when nothing was resolved.
    api_key_origin: ApiKeyOrigin | None = None

    def payload_summary(self) -> PayloadSummary:
        return PayloadSummary(
            model=self.model,
            message_count=len(self.messages),
            approx_chars=sum(len(m.content or "") for m in self.messages),
            temperature=self.temperature,
            has_tools=self.has_tools,
            tool_choice=self.tool_choice,
        )


class IncompleteStreamError(RuntimeError):
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            f"Model stream from {provider} ({model}) ended without a finish reason."
        )


class BackendError(RuntimeError):
    def __init__(
        self,
        *,
        provider: str,
        endpoint: str,
        status: int | None,
        reason: str | None,
        headers: Mapping[str, str] | None,
        body_text: str | None,
        parsed_error: str | None,
        model: str,
        payload_summary: PayloadSummary,
        api_key_origin: ApiKeyOrigin | None = None,
    ) -> None:
        self.provider = provider
        self.endpoint = endpoint
        self.status = status
        self.reason = reason
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body_text = body_text or ""
        self.parsed_error = parsed_error
        self.model = model
        self.payload_summary = payload_summary
        self.api_key_origin = api_key_origin
        super().__init__(self._fmt())

    @property
    def is_context_too_long(self) -> bool:
        if self.status not in {HTTPStatus.BAD_REQUEST, HTTPStatus.UNPROCESSABLE_ENTITY}:
            return False
        body = (self.body_text or "").lower()
        return any(s in body for s in _CONTEXT_TOO_LONG_SUBSTRINGS)

    @property
    def is_response_too_long(self) -> bool:
        if self.status != HTTPStatus.UNPROCESSABLE_ENTITY:
            return False
        body = (self.body_text or "").lower()
        return any(s in body for s in _RESPONSE_TOO_LONG_SUBSTRINGS)

    @property
    def is_invalid_model(self) -> bool:
        if self.status != HTTPStatus.BAD_REQUEST:
            return False
        body = (self.body_text or "").lower()
        return any(s in body for s in _INVALID_MODEL_SUBSTRINGS)

    def _fmt(self) -> str:
        if self.status == HTTPStatus.UNAUTHORIZED:
            origin = (
                f" (from {self.api_key_origin.describe()})"
                if self.api_key_origin
                else ""
            )
            return f"Invalid API key{origin}. Please check your API key and try again."

        if self.status == HTTPStatus.TOO_MANY_REQUESTS:
            return "Rate limit exceeded. Please wait a moment before trying again."

        if self.is_invalid_model:
            lines = [
                f"Model '{self.model}' is not available on {self.provider}.",
                "Switch to another configured model with /model, "
                "or fix the model name with /config.",
            ]
            if self.parsed_error:
                lines.append(f"Provider message: {self.parsed_error}")
            return "\n".join(lines)

        rid = self.headers.get("x-request-id") or self.headers.get("request-id")
        if self.status:
            try:
                status_label = f"{self.status} {HTTPStatus(self.status).phrase}"
            except ValueError:
                status_label = str(self.status)
        else:
            status_label = "N/A"
        parts = [
            f"LLM backend error [{self.provider}]",
            f"  status: {status_label}",
            f"  reason: {self.reason or 'N/A'}",
            f"  request_id: {rid or 'N/A'}",
            f"  endpoint: {self.endpoint}",
            f"  model: {self.model}",
            f"  provider_message: {self.parsed_error or 'N/A'}",
            f"  body_excerpt: {self._excerpt(self.body_text)}",
            f"  payload_summary: {self.payload_summary.model_dump_json(exclude_none=True)}",
        ]
        return "\n".join(parts)

    @staticmethod
    def _excerpt(s: str, *, n: int = 400) -> str:
        s = s.strip().replace("\n", " ")
        return s[:n] + ("…" if len(s) > n else "")


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: ErrorDetail | dict[str, Any] | None = None
    message: str | None = None
    detail: str | None = None

    @property
    def primary_message(self) -> str | None:
        if e := self.error:
            match e:
                case {"message": str(m)}:
                    return m
                case {"type": str(t)}:
                    return f"Error: {t}"
                case ErrorDetail(message=str(m)):
                    return m
        if m := self.message:
            return m
        if d := self.detail:
            return d
        return None


class BackendErrorBuilder:
    @classmethod
    def build_stream_error(
        cls, call: ModelCall, *, status: int | None, error_type: str, error_message: str
    ) -> BackendError:
        return cls._build(
            call,
            status=status,
            reason=error_type,
            headers=None,
            body_text=json.dumps({
                "error": {"type": error_type, "message": error_message}
            }),
            parsed_error=error_message,
        )

    @classmethod
    def build_http_error(
        cls, call: ModelCall, *, error: Exception, response: httpx.Response
    ) -> BackendError:
        """Build a BackendError from an HTTP error.

        `response` is the HTTP response carried by `error`; the caller extracts
        it since each client library stores it under a different attribute.
        """
        body_text = cls._read_response_body(response, error)

        return cls._build(
            call,
            status=response.status_code,
            reason=response.reason_phrase,
            headers=response.headers,
            body_text=body_text,
            parsed_error=cls._parse_provider_error(body_text),
        )

    @classmethod
    def build_request_error(
        cls, call: ModelCall, *, error: httpx.RequestError | httpx.StreamError
    ) -> BackendError:
        return cls._build(
            call,
            status=None,
            reason=str(error) or repr(error),
            headers={},
            body_text=None,
            parsed_error="Network error",
        )

    @staticmethod
    def _build(
        call: ModelCall,
        *,
        status: int | None,
        reason: str | None,
        headers: Mapping[str, str] | None,
        body_text: str | None,
        parsed_error: str | None,
    ) -> BackendError:
        return BackendError(
            provider=call.provider,
            endpoint=call.endpoint,
            status=status,
            reason=reason,
            headers=headers,
            body_text=body_text,
            parsed_error=parsed_error,
            model=call.model,
            payload_summary=call.payload_summary(),
            api_key_origin=call.api_key_origin,
        )

    @staticmethod
    def _read_response_body(response: httpx.Response, error: Exception) -> str | None:
        try:
            response.read()
            return response.text
        except Exception:
            pass
        if body := getattr(error, "body", None):
            return body
        return str(error)

    @staticmethod
    def _parse_provider_error(body_text: str | None) -> str | None:
        if not body_text:
            return None
        try:
            data = json.loads(body_text)
            error_model = ErrorResponse.model_validate(data)
            return error_model.primary_message
        except (json.JSONDecodeError, ValidationError):
            return None
