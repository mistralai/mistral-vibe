from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInGateway,
    BrowserSignInPollResult,
    BrowserSignInProcess,
)
from vibe.setup.auth.browser_sign_in import BrowserSignInService

BrowserSignInPollScript = tuple[BrowserSignInPollResult | BrowserSignInError, ...]
PollWaiter = Callable[[int], Awaitable[None]]


@dataclass
class ExchangeRequestPayload:
    process_id: str
    exchange_token: str
    code_verifier: str


class FakeBrowserSignInGateway(BrowserSignInGateway):
    def __init__(
        self,
        *,
        process: BrowserSignInProcess | None = None,
        processes: list[BrowserSignInProcess] | None = None,
        start_error: BrowserSignInError | None = None,
        poll_results: list[BrowserSignInPollResult | BrowserSignInError] | None = None,
        exchange_result: str = "sk-browser-key",
        exchange_error: BrowserSignInError | None = None,
        wait_before_poll_result: PollWaiter | None = None,
    ) -> None:
        if process is not None and processes is not None:
            msg = "FakeBrowserSignInGateway accepts either process or processes."
            raise AssertionError(msg)

        self._processes = list(processes or ([] if process is None else [process]))
        self._start_error = start_error
        self._poll_results = list(poll_results or [])
        self.exchange_result = exchange_result
        self.exchange_error = exchange_error
        self._wait_before_poll_result = wait_before_poll_result
        self.code_challenges: list[str] = []
        self.polled_urls: list[str] = []
        self.exchange_requests: list[ExchangeRequestPayload] = []
        self.closed = False
        self.poll_calls = 0
        self.process_number = 0

    async def create_process(self, code_challenge: str) -> BrowserSignInProcess:
        self.code_challenges.append(code_challenge)
        if self._start_error is not None:
            raise self._start_error
        if not self._processes:
            msg = "FakeBrowserSignInGateway requires at least one scripted process."
            raise AssertionError(msg)

        self.process_number += 1
        return self._processes.pop(0)

    async def poll(self, poll_url: str) -> BrowserSignInPollResult:
        self.polled_urls.append(poll_url)
        self.poll_calls += 1
        if self._wait_before_poll_result is not None:
            await self._wait_before_poll_result(self.poll_calls)
        if not self._poll_results:
            msg = "FakeBrowserSignInGateway requires scripted poll results."
            raise AssertionError(msg)
        result = self._poll_results.pop(0)
        if isinstance(result, BrowserSignInError):
            raise result
        return result

    async def exchange(
        self, process_id: str, exchange_token: str, code_verifier: str
    ) -> str:
        self.exchange_requests.append(
            ExchangeRequestPayload(
                process_id=process_id,
                exchange_token=exchange_token,
                code_verifier=code_verifier,
            )
        )
        if self.exchange_error is not None:
            raise self.exchange_error
        return self.exchange_result

    async def aclose(self) -> None:
        self.closed = True


def build_sign_in_process(
    now: datetime, process_id: str = "process-1"
) -> BrowserSignInProcess:
    fragment = urlencode({
        "process_id": process_id,
        "complete_token": f"complete-token-{process_id}",
        "state": f"state-{process_id}",
    })
    return BrowserSignInProcess(
        process_id=process_id,
        sign_in_url=(
            f"https://console.mistral.ai/codestral/cli/authenticate#{fragment}"
        ),
        poll_url=(
            f"https://console.mistral.ai/api/vibe/sign-in/poll/poll-token-{process_id}"
        ),
        expires_at=now + timedelta(minutes=5),
    )


def build_poll_failed_error() -> BrowserSignInError:
    return BrowserSignInError(
        "Browser sign-in status could not be retrieved.",
        code=BrowserSignInErrorCode.POLL_FAILED,
    )


def build_completed_poll_script(
    process_id: str = "process-1",
) -> BrowserSignInPollScript:
    return (
        BrowserSignInPollResult(status="pending"),
        BrowserSignInPollResult(
            status="completed", exchange_token=f"exchange-{process_id}"
        ),
    )


def build_expired_poll_script() -> BrowserSignInPollScript:
    return (BrowserSignInPollResult(status="expired"),)


def build_poll_failed_script() -> BrowserSignInPollScript:
    return (
        BrowserSignInPollResult(status="pending"),
        build_poll_failed_error(),
        build_poll_failed_error(),
        build_poll_failed_error(),
    )


def build_processes_and_poll_results(
    poll_scripts: list[BrowserSignInPollScript],
) -> tuple[
    list[BrowserSignInProcess], list[BrowserSignInPollResult | BrowserSignInError]
]:
    processes: list[BrowserSignInProcess] = []
    poll_results: list[BrowserSignInPollResult | BrowserSignInError] = []
    now = datetime(2026, 3, 16, tzinfo=UTC)

    for process_index, poll_script in enumerate(poll_scripts, start=1):
        process_id = f"process-{process_index}"
        processes.append(build_sign_in_process(now, process_id=process_id))
        poll_results.extend(poll_script)

    return processes, poll_results


async def noop_sleep(_: float) -> None:
    return None


def build_browser_sign_in_service_factory(
    poll_scripts: list[BrowserSignInPollScript],
    *,
    exchange_result: str = "sk-browser-onboarding-test-key",
    open_browser: Callable[[str], bool] | None = None,
    raise_on_browser_open_failure: bool = True,
    sleep: Callable[[float], Awaitable[None]] = noop_sleep,
    now: Callable[[], datetime] | None = None,
    wait_before_poll_result: PollWaiter | None = None,
) -> tuple[
    FakeBrowserSignInGateway,
    Callable[[], BrowserSignInService],
    list[BrowserSignInService],
]:
    processes, poll_results = build_processes_and_poll_results(poll_scripts)
    gateway = FakeBrowserSignInGateway(
        processes=processes,
        poll_results=poll_results,
        exchange_result=exchange_result,
        wait_before_poll_result=wait_before_poll_result,
    )
    created_services: list[BrowserSignInService] = []

    def build_service() -> BrowserSignInService:
        service = BrowserSignInService(
            gateway,
            open_browser=open_browser or (lambda _: True),
            raise_on_browser_open_failure=raise_on_browser_open_failure,
            sleep=sleep,
            now=now
            or (
                lambda: (
                    datetime(2026, 3, 16, tzinfo=UTC)
                    + timedelta(seconds=gateway.poll_calls)
                )
            ),
            poll_interval=0,
        )
        created_services.append(service)
        return service

    return gateway, build_service, created_services
