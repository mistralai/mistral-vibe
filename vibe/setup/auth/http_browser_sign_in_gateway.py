from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import posixpath
import types
from typing import Any, Literal, TypedDict, cast
from urllib.parse import SplitResult, unquote, urlsplit

import httpx

from vibe.core.logger import logger
from vibe.setup.auth.browser_sign_in_gateway import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInGateway,
    BrowserSignInPollResult,
    BrowserSignInProcess,
)


class CreateProcessPayload(TypedDict):
    process_id: str
    sign_in_url: str
    poll_url: str
    expires_at: str


class PollPayload(TypedDict, total=False):
    status: Literal["pending", "completed", "expired", "denied", "error"]
    exchange_token: str
    message: str


class ExchangePayload(TypedDict, total=False):
    api_key: str


HTTP_GONE = 410
_DEFAULT_PORTS = {"http": 80, "https": 443}


class HttpBrowserSignInGateway(BrowserSignInGateway):
    def __init__(
        self,
        browser_base_url: str,
        api_base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._browser_base_url = browser_base_url.rstrip("/")
        self._api_base_url = api_base_url.rstrip("/")
        self._client = client
        self._should_manage_client = client is None

    async def __aenter__(self) -> HttpBrowserSignInGateway:
        self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._should_manage_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_process(self, code_challenge: str) -> BrowserSignInProcess:
        message = "Failed to start browser sign-in."
        code = BrowserSignInErrorCode.START_FAILED
        try:
            response = await self._ensure_client().post(
                f"{self._api_base_url}/vibe/sign-in",
                json={
                    "code_challenge": code_challenge,
                    "code_challenge_method": "S256",
                },
            )
        except httpx.HTTPError as err:
            logger.warning(
                "Browser sign-in start request failed for api_base_url=%s: %s",
                self._api_base_url,
                err,
                exc_info=True,
            )
            raise BrowserSignInError(message, code=code) from err

        if not response.is_success:
            logger.warning(
                "Browser sign-in start request returned status_code=%s for api_base_url=%s",
                response.status_code,
                self._api_base_url,
            )
            raise BrowserSignInError(message, code=code)

        payload = _response_json_or_raise(response, message=message, code=code)
        data = cast(CreateProcessPayload, payload)
        try:
            return BrowserSignInProcess(
                process_id=data["process_id"],
                sign_in_url=_validate_url_against_base_url(
                    data["sign_in_url"],
                    base_url=self._browser_base_url,
                    message=message,
                    code=code,
                ),
                poll_url=_validate_url_against_base_url(
                    data["poll_url"],
                    base_url=self._api_base_url,
                    message=message,
                    code=code,
                ),
                expires_at=_parse_expires_at(data["expires_at"]),
            )
        except BrowserSignInError:
            sign_in_url_details, poll_url_details = _build_start_payload_log_details(
                data
            )
            logger.warning(
                "Browser sign-in start payload validation failed for browser_base_url=%s api_base_url=%s sign_in_url=%s poll_url=%s",
                self._browser_base_url,
                self._api_base_url,
                sign_in_url_details,
                poll_url_details,
                exc_info=True,
            )
            raise
        except (KeyError, TypeError, ValueError) as err:
            sign_in_url_details, poll_url_details = _build_start_payload_log_details(
                payload
            )
            logger.warning(
                "Browser sign-in start payload parsing failed for api_base_url=%s payload_keys=%s sign_in_url=%s poll_url=%s",
                self._api_base_url,
                sorted(str(key) for key in payload),
                sign_in_url_details,
                poll_url_details,
                exc_info=True,
            )
            raise BrowserSignInError(message, code=code) from err

    async def poll(self, poll_url: str) -> BrowserSignInPollResult:
        message = "Browser sign-in status could not be retrieved."
        code = BrowserSignInErrorCode.POLL_FAILED
        validated_poll_url = _validate_url_against_base_url(
            poll_url, base_url=self._api_base_url, message=message, code=code
        )
        try:
            response = await self._ensure_client().get(validated_poll_url)
        except httpx.HTTPError as err:
            raise BrowserSignInError(message, code=code) from err

        if response.status_code == HTTP_GONE:
            return BrowserSignInPollResult(status="expired")

        if not response.is_success:
            raise BrowserSignInError(message, code=code)

        raw_payload = _response_json_or_raise(response, message=message, code=code)
        payload = cast(PollPayload, raw_payload)
        status = payload.get("status")
        if status not in {"pending", "completed", "expired", "denied", "error"}:
            raise BrowserSignInError(
                "Browser sign-in returned an unknown state.",
                code=BrowserSignInErrorCode.UNKNOWN_STATE,
            )

        return BrowserSignInPollResult(
            status=status,
            exchange_token=payload.get("exchange_token"),
            message=payload.get("message"),
        )

    async def exchange(
        self, process_id: str, exchange_token: str, code_verifier: str
    ) -> str:
        message = "Failed to exchange browser sign-in for an API key."
        code = BrowserSignInErrorCode.EXCHANGE_FAILED
        try:
            response = await self._ensure_client().post(
                f"{self._api_base_url}/vibe/sign-in/{process_id}/exchange",
                json={"exchange_token": exchange_token, "code_verifier": code_verifier},
            )
        except httpx.HTTPError as err:
            raise BrowserSignInError(message, code=code) from err

        if not response.is_success:
            raise BrowserSignInError(message, code=code)

        raw_payload = _response_json_or_raise(response, message=message, code=code)
        payload = cast(ExchangePayload, raw_payload)
        if api_key := payload.get("api_key"):
            return api_key
        raise BrowserSignInError(
            "Browser sign-in exchange did not return an API key.",
            code=BrowserSignInErrorCode.MISSING_API_KEY,
        )

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient()
            self._should_manage_client = True
        return self._client


def _parse_expires_at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _validate_url_against_base_url(
    value: str, *, base_url: str, message: str, code: BrowserSignInErrorCode
) -> str:
    current_url = urlsplit(value)
    base = urlsplit(base_url)
    safe_url_details = _build_safe_url_log_details(value)
    try:
        current_origin = _normalized_origin(current_url)
        base_origin = _normalized_origin(base)
    except ValueError as err:
        logger.warning(
            "Browser sign-in URL origin validation failed for returned_url=%s expected_base_url=%s",
            safe_url_details,
            base_url,
            exc_info=True,
        )
        raise BrowserSignInError(message, code=code) from err
    if current_origin != base_origin:
        logger.warning(
            "Browser sign-in URL host validation failed for returned_url=%s expected_base_url=%s",
            safe_url_details,
            base_url,
        )
        raise BrowserSignInError(message, code=code)
    if not _is_path_under_base_path(current_url.path, base.path):
        logger.warning(
            "Browser sign-in URL path validation failed for returned_url=%s expected_base_url=%s",
            safe_url_details,
            base_url,
        )
        raise BrowserSignInError(message, code=code)
    return value


def _is_path_under_base_path(path: str, base_path: str) -> bool:
    normalized_path = _normalize_url_path(path)
    normalized_base_path = _normalize_url_path(base_path).rstrip("/")
    if not normalized_base_path:
        return True
    if normalized_path == normalized_base_path:
        return True
    return normalized_path.startswith(f"{normalized_base_path}/")


def _normalized_origin(parsed: SplitResult) -> tuple[str, str | None, int | None]:
    scheme = parsed.scheme.lower()
    port = parsed.port
    return scheme, parsed.hostname, _effective_port(scheme, port)


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return _DEFAULT_PORTS.get(scheme)


def _normalize_url_path(path: str) -> str:
    if not path:
        return "/"

    decoded_path = unquote(path)
    return posixpath.normpath(decoded_path)


def _build_start_payload_log_details(payload: Mapping[str, object]) -> tuple[str, str]:
    return (
        _build_safe_url_log_details(payload.get("sign_in_url")),
        _build_safe_url_log_details(payload.get("poll_url")),
    )


def _response_json_or_raise(
    response: httpx.Response, *, message: str, code: BrowserSignInErrorCode
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as err:
        raise BrowserSignInError(message, code=code) from err

    if not isinstance(payload, Mapping):
        raise BrowserSignInError(message, code=code)

    return dict(payload)


def _build_safe_url_log_details(value: object) -> str:
    if not isinstance(value, str):
        return "unavailable"

    parsed = urlsplit(value)
    return (
        f"scheme={parsed.scheme or None!r} "
        f"hostname={parsed.hostname!r} "
        f"port={_safe_port(parsed)!r} "
        f"has_path={bool(parsed.path)} "
        f"has_query={bool(parsed.query)} "
        f"has_fragment={bool(parsed.fragment)}"
    )


def _safe_port(parsed: SplitResult) -> int | None:
    try:
        return parsed.port
    except ValueError:
        return None
