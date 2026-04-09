from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from typing import cast
from urllib.parse import urlencode

import pytest

from tests.browser_sign_in.stubs import (
    StubBrowserSignInGateway,
    build_poll_failed_error,
    build_sign_in_process,
    noop_sleep,
)
from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInPollResult,
    BrowserSignInProcess,
    BrowserSignInService,
)

TEST_NOW = datetime(2026, 3, 16, tzinfo=UTC)

TEST_PROCESS_ID = "process-1"
TEST_SIGN_IN_URL = "https://console.mistral.ai/codestral/cli/authenticate#" + urlencode({
    "process_id": TEST_PROCESS_ID,
    "complete_token": f"complete-token-{TEST_PROCESS_ID}",
    "state": f"state-{TEST_PROCESS_ID}",
})
TEST_POLL_URL = "https://api.mistral.ai/api/vibe/sign-in/poll/poll-token-process-1"


def build_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_test_service(
    *,
    poll_results: list[BrowserSignInPollResult | BrowserSignInError],
    exchange_error: BrowserSignInError | None = None,
    open_browser: Callable[[str], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = noop_sleep,
    now: Callable[[], datetime] | None = None,
    process_time: datetime = TEST_NOW,
) -> tuple[StubBrowserSignInGateway, BrowserSignInService]:
    gateway = StubBrowserSignInGateway(
        process=build_sign_in_process(process_time),
        poll_results=poll_results,
        exchange_error=exchange_error,
    )
    service = BrowserSignInService(
        gateway,
        open_browser=open_browser or (lambda _: True),
        sleep=sleep,
        now=now or (lambda: process_time),
        poll_interval=0,
    )
    return gateway, service


@pytest.mark.asyncio
async def test_authenticate_returns_api_key_after_pending_poll() -> None:
    opened_urls: list[str] = []
    statuses: list[str] = []
    gateway, service = build_test_service(
        poll_results=[
            BrowserSignInPollResult(status="pending"),
            BrowserSignInPollResult(status="completed", exchange_token="exchange-1"),
        ],
        open_browser=lambda url: opened_urls.append(url) or True,
    )

    api_key = await service.authenticate(status_callback=statuses.append)

    code_verifier = gateway.exchange_requests[0].code_verifier
    assert gateway.code_challenges == [build_code_challenge(code_verifier)]
    assert api_key == "sk-browser-key"
    assert opened_urls == [TEST_SIGN_IN_URL]
    assert statuses == [
        "opening_browser",
        "waiting_for_browser_sign_in",
        "exchanging",
        "completed",
    ]
    assert gateway.polled_urls == [TEST_POLL_URL, TEST_POLL_URL]
    assert gateway.exchange_requests[0].exchange_token == "exchange-1"


@pytest.mark.asyncio
async def test_authenticate_raises_when_polling_expires() -> None:
    opened_urls: list[str] = []
    _, service = build_test_service(
        poll_results=[BrowserSignInPollResult(status="expired")],
        open_browser=lambda url: opened_urls.append(url) or True,
    )

    with pytest.raises(BrowserSignInError, match="expired"):
        await service.authenticate()

    assert opened_urls == [TEST_SIGN_IN_URL]


@pytest.mark.asyncio
async def test_authenticate_retries_after_transient_poll_failure() -> None:
    gateway, service = build_test_service(
        poll_results=[
            build_poll_failed_error(),
            BrowserSignInPollResult(status="completed", exchange_token="exchange-1"),
        ]
    )

    api_key = await service.authenticate()

    assert api_key == "sk-browser-key"
    assert gateway.polled_urls == [TEST_POLL_URL, TEST_POLL_URL]


@pytest.mark.asyncio
async def test_authenticate_fails_after_three_consecutive_poll_failures() -> None:
    _, service = build_test_service(
        poll_results=[
            build_poll_failed_error(),
            build_poll_failed_error(),
            build_poll_failed_error(),
        ]
    )

    with pytest.raises(
        BrowserSignInError, match="status could not be retrieved"
    ) as err:
        await service.authenticate()

    assert err.value.code is BrowserSignInErrorCode.POLL_FAILED


@pytest.mark.asyncio
async def test_authenticate_resets_poll_failure_streak_after_successful_poll() -> None:
    gateway, service = build_test_service(
        poll_results=[
            build_poll_failed_error(),
            BrowserSignInPollResult(status="pending"),
            build_poll_failed_error(),
            BrowserSignInPollResult(status="completed", exchange_token="exchange-1"),
        ]
    )

    api_key = await service.authenticate()

    assert api_key == "sk-browser-key"
    assert len(gateway.polled_urls) == 4


@pytest.mark.asyncio
async def test_authenticate_raises_on_unknown_poll_state() -> None:
    class UnknownStateGateway:
        def __init__(self) -> None:
            self.process = build_sign_in_process(TEST_NOW)

        async def create_process(self, code_challenge: str):
            return self.process

        async def poll(self, poll_url: str) -> BrowserSignInPollResult:
            return cast(
                BrowserSignInPollResult,
                SimpleNamespace(status="unexpected", exchange_token=None, message=None),
            )

        async def exchange(
            self, process_id: str, exchange_token: str, code_verifier: str
        ) -> str:
            return "sk-browser-key"

        async def aclose(self) -> None:
            return None

    service = BrowserSignInService(
        UnknownStateGateway(),
        open_browser=lambda _: True,
        sleep=noop_sleep,
        now=lambda: TEST_NOW,
        poll_interval=0,
    )

    with pytest.raises(BrowserSignInError, match="unknown state") as err:
        await service.authenticate()

    assert err.value.code is BrowserSignInErrorCode.UNKNOWN_STATE


@pytest.mark.asyncio
async def test_authenticate_raises_when_browser_cannot_be_opened() -> None:
    _, service = build_test_service(poll_results=[], open_browser=lambda _: False)

    with pytest.raises(BrowserSignInError, match="open browser"):
        await service.authenticate()


@pytest.mark.asyncio
async def test_authenticate_raises_when_exchange_fails() -> None:
    _, service = build_test_service(
        poll_results=[
            BrowserSignInPollResult(status="completed", exchange_token="exchange-1")
        ],
        exchange_error=BrowserSignInError(
            "Failed to exchange browser sign-in for an API key."
        ),
    )

    with pytest.raises(BrowserSignInError, match="exchange"):
        await service.authenticate()


@pytest.mark.asyncio
async def test_authenticate_can_be_cancelled_before_start() -> None:
    gateway, service = build_test_service(poll_results=[])
    task = asyncio.create_task(service.authenticate())
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert gateway.code_challenges == []
    assert gateway.exchange_requests == []


@pytest.mark.asyncio
async def test_authenticate_can_be_cancelled_while_waiting_for_sign_in() -> None:
    blocker = asyncio.Event()

    async def wait_forever(_: float) -> None:
        await blocker.wait()

    gateway, service = build_test_service(
        poll_results=[BrowserSignInPollResult(status="pending")], sleep=wait_forever
    )
    task = asyncio.create_task(service.authenticate())

    while not gateway.polled_urls:
        await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert gateway.exchange_requests == []


@pytest.mark.asyncio
async def test_authenticate_times_out_when_process_never_completes() -> None:
    current_time = TEST_NOW

    async def advance_time(_: float) -> None:
        nonlocal current_time
        current_time += timedelta(minutes=3)

    _, service = build_test_service(
        poll_results=[
            BrowserSignInPollResult(status="pending"),
            BrowserSignInPollResult(status="pending"),
        ],
        sleep=advance_time,
        now=lambda: current_time,
        process_time=current_time,
    )

    with pytest.raises(BrowserSignInError, match="timed out"):
        await service.authenticate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first_poll_result",
    [BrowserSignInPollResult(status="pending"), build_poll_failed_error()],
    ids=["pending", "transient_poll_failure"],
)
async def test_authenticate_caps_sleep_to_remaining_sign_in_lifetime(
    first_poll_result: BrowserSignInPollResult | BrowserSignInError,
) -> None:
    current_time = TEST_NOW
    sleep_durations: list[float] = []

    async def advance_time(duration: float) -> None:
        nonlocal current_time
        sleep_durations.append(duration)
        current_time += timedelta(seconds=duration)

    gateway = StubBrowserSignInGateway(
        process=BrowserSignInProcess(
            process_id=TEST_PROCESS_ID,
            sign_in_url=TEST_SIGN_IN_URL,
            poll_url=TEST_POLL_URL,
            expires_at=TEST_NOW + timedelta(seconds=1),
        ),
        poll_results=[first_poll_result],
    )
    service = BrowserSignInService(
        gateway,
        open_browser=lambda _: True,
        sleep=advance_time,
        now=lambda: current_time,
        poll_interval=3.0,
    )

    with pytest.raises(BrowserSignInError, match="timed out") as err:
        await service.authenticate()

    assert err.value.code is BrowserSignInErrorCode.TIMED_OUT
    assert sleep_durations == [1.0]


@pytest.mark.asyncio
async def test_aclose_closes_underlying_api() -> None:
    gateway, service = build_test_service(poll_results=[])

    await service.aclose()

    assert gateway.closed is True
