from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import logging
from urllib.parse import urlencode

import httpx
import pytest

from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    HttpBrowserSignInGateway,
)

AUTH_ORIGIN = "https://api.mistral.ai"
AUTH_BROWSER_BASE_URL = "https://console.mistral.ai"
AUTH_API_BASE_URL = AUTH_ORIGIN
TEST_PROCESS_ID = "process-1"
TEST_COMPLETE_TOKEN = "complete-token-1"
TEST_STATE = "state-1"
TEST_POLL_TOKEN = "poll-token-1"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def build_sign_in_url(
    *,
    process_id: str = TEST_PROCESS_ID,
    base_url: str = AUTH_BROWSER_BASE_URL,
    complete_token: str = TEST_COMPLETE_TOKEN,
    state: str = TEST_STATE,
) -> str:
    fragment = urlencode({
        "process_id": process_id,
        "complete_token": complete_token,
        "state": state,
    })
    return f"{base_url}/codestral/cli/authenticate#{fragment}"


def build_poll_url(
    *, poll_token: str = TEST_POLL_TOKEN, api_base_url: str = AUTH_API_BASE_URL
) -> str:
    return f"{api_base_url}/api/vibe/sign-in/poll/{poll_token}"


@asynccontextmanager
async def build_gateway(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    origin: str = AUTH_ORIGIN,
    browser_base_url: str = AUTH_BROWSER_BASE_URL,
    api_base_url: str = AUTH_API_BASE_URL,
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=origin
    ) as client:
        yield HttpBrowserSignInGateway(
            browser_base_url=browser_base_url, api_base_url=api_base_url, client=client
        )


@pytest.mark.asyncio
async def test_http_api_creates_process_with_pkce_payload() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)
    captured_body: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        assert request.url.path == "/vibe/sign-in"
        captured_body = request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": build_poll_url(),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        process = await gateway.create_process("challenge-123")

    assert process.process_id == TEST_PROCESS_ID
    assert process.sign_in_url == build_sign_in_url()
    assert process.poll_url == build_poll_url()
    assert captured_body is not None
    assert '"code_challenge":"challenge-123"' in captured_body
    assert '"code_challenge_method":"S256"' in captured_body


@pytest.mark.asyncio
async def test_http_api_polls_process_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/vibe/sign-in/poll/poll-token-1"
        return httpx.Response(
            200, json={"status": "completed", "exchange_token": "exchange-1"}
        )

    async with build_gateway(handler) as gateway:
        result = await gateway.poll(build_poll_url())

    assert result.status == "completed"
    assert result.exchange_token == "exchange-1"


@pytest.mark.asyncio
async def test_http_api_maps_410_poll_response_to_expired_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/vibe/sign-in/poll/poll-token-1"
        return httpx.Response(410)

    async with build_gateway(handler) as gateway:
        result = await gateway.poll(build_poll_url())

    assert result.status == "expired"
    assert result.exchange_token is None


@pytest.mark.asyncio
async def test_http_api_exchanges_token_for_api_key() -> None:
    captured_body: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        assert request.url.path == "/vibe/sign-in/process-1/exchange"
        captured_body = request.content.decode("utf-8")
        return httpx.Response(200, json={"api_key": "sk-browser-key"})

    async with build_gateway(handler) as gateway:
        api_key = await gateway.exchange("process-1", "exchange-1", "verifier-1")

    assert api_key == "sk-browser-key"
    assert captured_body is not None
    assert '"exchange_token":"exchange-1"' in captured_body
    assert '"code_verifier":"verifier-1"' in captured_body


@pytest.mark.asyncio
async def test_http_api_translates_transport_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_assigns_poll_failed_code_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with build_gateway(handler) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll(build_poll_url())

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_assigns_poll_failed_code_on_invalid_poll_url() -> None:
    async with build_gateway(lambda _: httpx.Response(200)) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll("https://evil.example/poll/secret-1")

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_translates_non_json_start_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_translates_missing_start_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": build_poll_url(),
            },
        )

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_accepts_poll_url_under_configured_api_base_url() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": build_poll_url(),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        process = await gateway.create_process("challenge-123")

    assert process.poll_url == build_poll_url()


@pytest.mark.asyncio
async def test_http_api_accepts_poll_url_under_configured_api_base_path() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/vibe/sign-in"
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": build_poll_url(
                    poll_token=TEST_POLL_TOKEN, api_base_url="https://api.mistral.ai/v1"
                ),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(
        handler,
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        process = await gateway.create_process("challenge-123")

    assert process.poll_url == build_poll_url(api_base_url="https://api.mistral.ai/v1")


@pytest.mark.asyncio
async def test_http_api_accepts_same_origin_urls_with_explicit_default_https_ports() -> (
    None
):
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://console.mistral.ai:443"
                ),
                "poll_url": build_poll_url(api_base_url="https://api.mistral.ai:443"),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        process = await gateway.create_process("challenge-123")

    assert process.sign_in_url == build_sign_in_url(
        base_url="https://console.mistral.ai:443"
    )
    assert process.poll_url == build_poll_url(api_base_url="https://api.mistral.ai:443")


@pytest.mark.asyncio
async def test_http_api_accepts_poll_url_without_explicit_default_https_port_when_base_has_one() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/vibe/sign-in/poll/poll-token-1"
        return httpx.Response(
            200, json={"status": "completed", "exchange_token": "exchange-1"}
        )

    async with build_gateway(
        handler,
        origin="https://api.mistral.ai:443",
        api_base_url="https://api.mistral.ai:443",
    ) as gateway:
        result = await gateway.poll(build_poll_url())

    assert result.status == "completed"
    assert result.exchange_token == "exchange-1"


@pytest.mark.asyncio
async def test_http_api_accepts_sign_in_url_under_configured_browser_base_url() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": build_poll_url(),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        process = await gateway.create_process("challenge-123")

    assert process.sign_in_url == build_sign_in_url()


@pytest.mark.asyncio
async def test_http_api_rejects_sign_in_url_outside_browser_base_url() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(base_url="https://evil.example"),
                "poll_url": build_poll_url(),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_sign_in_url_outside_browser_base_path_after_normalization() -> (
    None
):
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://console.mistral.ai/v1/.."
                ),
                "poll_url": build_poll_url(api_base_url="https://api.mistral.ai/v1"),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(
        handler,
        browser_base_url="https://console.mistral.ai/v1",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_sign_in_url_with_encoded_dot_segments_outside_browser_base_path() -> (
    None
):
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://console.mistral.ai/v1/%2e%2e"
                ),
                "poll_url": build_poll_url(api_base_url="https://api.mistral.ai/v1"),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(
        handler,
        browser_base_url="https://console.mistral.ai/v1",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_poll_url_outside_api_base_url() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": "https://evil.example/api/vibe/sign-in/poll/poll-token-1",
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_returned_poll_url_outside_api_base_path_after_normalization() -> (
    None
):
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://console.mistral.ai/v1"
                ),
                "poll_url": build_poll_url(
                    poll_token=TEST_POLL_TOKEN,
                    api_base_url="https://api.mistral.ai/v1/..",
                ),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(
        handler,
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
        browser_base_url="https://console.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_returned_poll_url_with_encoded_dot_segments_outside_api_base_path() -> (
    None
):
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://console.mistral.ai/v1"
                ),
                "poll_url": build_poll_url(
                    poll_token=TEST_POLL_TOKEN,
                    api_base_url="https://api.mistral.ai/v1/%2e%2e",
                ),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(
        handler,
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
        browser_base_url="https://console.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_poll_url_outside_api_base_path() -> None:
    async with build_gateway(
        lambda _: httpx.Response(200),
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll(
                "https://api.mistral.ai/v1evil/api/vibe/sign-in/poll/poll-token-1"
            )

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_rejects_poll_url_outside_api_base_path_after_normalization() -> (
    None
):
    async with build_gateway(
        lambda _: httpx.Response(200),
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll(
                "https://api.mistral.ai/v1/../api/vibe/sign-in/poll/poll-token-1"
            )

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_translates_invalid_returned_poll_url_port() -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(),
                "poll_url": "https://api.mistral.ai:99999/api/vibe/sign-in/poll/poll-token-1",
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="start browser sign-in") as err:
            await gateway.create_process("challenge-123")

    assert err.value.code is BrowserSignInErrorCode.START_FAILED


@pytest.mark.asyncio
async def test_http_api_assigns_poll_failed_code_on_invalid_poll_url_port() -> None:
    async with build_gateway(lambda _: httpx.Response(200)) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll(
                "https://api.mistral.ai:99999/api/vibe/sign-in/poll/poll-token-1"
            )

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_translates_non_json_poll_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async with build_gateway(handler) as gateway:
        with pytest.raises(
            BrowserSignInError, match="status could not be retrieved"
        ) as err:
            await gateway.poll(build_poll_url())

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_http_api_translates_non_json_exchange_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    async with build_gateway(handler) as gateway:
        with pytest.raises(BrowserSignInError, match="exchange browser sign-in") as err:
            await gateway.exchange("process-1", "exchange-1", "verifier-1")

    assert err.value.code is BrowserSignInErrorCode.EXCHANGE_FAILED


@pytest.mark.asyncio
async def test_http_api_does_not_log_sign_in_or_poll_secrets_on_start_validation_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 3, 16, tzinfo=UTC)
    complete_token = "complete-token-secret"
    state = "state-secret"
    poll_token = "poll-token-secret"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "process_id": TEST_PROCESS_ID,
                "sign_in_url": build_sign_in_url(
                    base_url="https://evil.example",
                    complete_token=complete_token,
                    state=state,
                ),
                "poll_url": build_poll_url(poll_token=poll_token),
                "expires_at": _iso(now + timedelta(minutes=5)),
            },
        )

    async with build_gateway(handler) as gateway:
        with caplog.at_level(logging.WARNING, logger="vibe"):
            with pytest.raises(BrowserSignInError, match="start browser sign-in"):
                await gateway.create_process("challenge-123")

    assert complete_token not in caplog.text
    assert state not in caplog.text
    assert poll_token not in caplog.text


@pytest.mark.asyncio
async def test_http_api_does_not_log_poll_secret_on_poll_url_validation_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    poll_token = "poll-token-secret"

    async with build_gateway(
        lambda _: httpx.Response(200),
        origin="https://api.mistral.ai",
        api_base_url="https://api.mistral.ai/v1",
    ) as gateway:
        with caplog.at_level(logging.WARNING, logger="vibe"):
            with pytest.raises(
                BrowserSignInError, match="status could not be retrieved"
            ):
                await gateway.poll(
                    f"https://api.mistral.ai/v1evil/api/vibe/sign-in/poll/{poll_token}"
                )

    assert poll_token not in caplog.text
