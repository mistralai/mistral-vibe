from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.core.types import AvailableTool, LLMMessage, StrToolChoice


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
        super().__init__(self._fmt())

    def _fmt(self) -> str:
        if self.status == HTTPStatus.UNAUTHORIZED:
            return "Invalid API key. Please check your API key and try again."

        if self.status == HTTPStatus.TOO_MANY_REQUESTS:
            return "Rate limit exceeded. Please wait a moment before trying again."

        rid = self.headers.get("x-request-id") or self.headers.get("request-id")
        status_label = (
            f"{self.status} {HTTPStatus(self.status).phrase}" if self.status else "N/A"
        )
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
    def build_http_error(
        cls,
        *,
        provider: str,
        endpoint: str,
        response: httpx.Response,
        headers: Mapping[str, str] | None,
        model: str,
        messages: list[LLMMessage],
        temperature: float,
        has_tools: bool,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> BackendError:
        try:
            body_text = response.text
        except Exception:  # On streaming responses, we can't read the body
            body_text = None

        return BackendError(
            provider=provider,
            endpoint=endpoint,
            status=response.status_code,
            reason=response.reason_phrase,
            headers=headers or {},
            body_text=body_text,
            parsed_error=cls._parse_provider_error(body_text),
            model=model,
            payload_summary=cls._payload_summary(
                model, messages, temperature, has_tools, tool_choice
            ),
        )

    @classmethod
    def build_request_error(
        cls,
        *,
        provider: str,
        endpoint: str,
        error: httpx.RequestError,
        model: str,
        messages: list[LLMMessage],
        temperature: float,
        has_tools: bool,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> BackendError:
        return BackendError(
            provider=provider,
            endpoint=endpoint,
            status=None,
            reason=str(error) or repr(error),
            headers={},
            body_text=None,
            parsed_error="Network error",
            model=model,
            payload_summary=cls._payload_summary(
                model, messages, temperature, has_tools, tool_choice
            ),
        )

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

    @staticmethod
    def _payload_summary(
        model_name: str,
        messages: list[LLMMessage],
        temperature: float,
        has_tools: bool,
        tool_choice: StrToolChoice | AvailableTool | None,
    ) -> PayloadSummary:
        total_chars = sum(len(m.content or "") for m in messages)
        return PayloadSummary(
            model=model_name,
            message_count=len(messages),
            approx_chars=total_chars,
            temperature=temperature,
            has_tools=has_tools,
            tool_choice=tool_choice,
        )
