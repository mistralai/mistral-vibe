from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from vibe.setup.auth import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInGateway,
    BrowserSignInPollResult,
    BrowserSignInProcess,
)


@dataclass
class ExchangeRequestPayload:
    process_id: str
    exchange_token: str
    code_verifier: str


class StubBrowserSignInGateway(BrowserSignInGateway):
    def __init__(
        self,
        *,
        process: BrowserSignInProcess | None = None,
        processes: list[BrowserSignInProcess] | None = None,
        poll_results: list[BrowserSignInPollResult | BrowserSignInError] | None = None,
        exchange_result: str = "sk-browser-key",
        exchange_error: BrowserSignInError | None = None,
    ) -> None:
        if process is not None and processes is not None:
            msg = "StubBrowserSignInGateway accepts either process or processes."
            raise AssertionError(msg)

        self._processes = list(processes or ([] if process is None else [process]))
        self._poll_results = list(poll_results or [])
        self.exchange_result = exchange_result
        self.exchange_error = exchange_error
        self.code_challenges: list[str] = []
        self.polled_urls: list[str] = []
        self.exchange_requests: list[ExchangeRequestPayload] = []
        self.closed = False
        self.process_number = 0

    async def create_process(self, code_challenge: str) -> BrowserSignInProcess:
        self.code_challenges.append(code_challenge)
        if not self._processes:
            msg = "StubBrowserSignInGateway requires at least one scripted process."
            raise AssertionError(msg)

        self.process_number += 1
        return self._processes.pop(0)

    async def poll(self, poll_url: str) -> BrowserSignInPollResult:
        self.polled_urls.append(poll_url)
        if not self._poll_results:
            msg = "StubBrowserSignInGateway requires scripted poll results."
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
            f"https://api.mistral.ai/api/vibe/sign-in/poll/poll-token-{process_id}"
        ),
        expires_at=now + timedelta(minutes=5),
    )


def build_poll_failed_error() -> BrowserSignInError:
    return BrowserSignInError(
        "Browser sign-in status could not be retrieved.",
        code=BrowserSignInErrorCode.POLL_FAILED,
    )


async def noop_sleep(_: float) -> None:
    return None
