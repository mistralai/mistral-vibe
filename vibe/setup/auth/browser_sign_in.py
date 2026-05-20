from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import secrets
import webbrowser

from vibe.setup.auth.browser_sign_in_gateway import (
    BrowserSignInError,
    BrowserSignInErrorCode,
    BrowserSignInGateway,
)


class BrowserSignInStatus(StrEnum):
    OPENING_BROWSER = "opening_browser"
    WAITING_FOR_BROWSER_SIGN_IN = "waiting_for_browser_sign_in"
    EXCHANGING = "exchanging"
    COMPLETED = "completed"


StatusCallback = Callable[[BrowserSignInStatus], None]
BrowserOpener = Callable[[str], bool]
SleepFn = Callable[[float], Awaitable[None]]
NowFn = Callable[[], datetime]


@dataclass(frozen=True)
class BrowserSignInAttempt:
    process_id: str
    sign_in_url: str
    poll_url: str
    expires_at: datetime
    code_verifier: str


class BrowserSignInService:
    _max_consecutive_poll_failures = 3

    def __init__(
        self,
        gateway: BrowserSignInGateway,
        *,
        open_browser: BrowserOpener | None = None,
        sleep: SleepFn = asyncio.sleep,
        now: NowFn | None = None,
        poll_interval: float = 3.0,
    ) -> None:
        self._gateway = gateway
        self._open_browser = open_browser or webbrowser.open
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(UTC))
        self._poll_interval = poll_interval

    async def aclose(self) -> None:
        await self._gateway.aclose()

    async def start_attempt(self) -> BrowserSignInAttempt:
        verifier, challenge = _generate_pkce_pair()
        process = await self._gateway.create_process(challenge)
        return BrowserSignInAttempt(
            process_id=process.process_id,
            sign_in_url=process.sign_in_url,
            poll_url=process.poll_url,
            expires_at=process.expires_at,
            code_verifier=verifier,
        )

    async def complete_attempt(
        self,
        attempt: BrowserSignInAttempt,
        status_callback: StatusCallback | None = None,
    ) -> str:
        self._emit(status_callback, BrowserSignInStatus.WAITING_FOR_BROWSER_SIGN_IN)
        exchange_token = await self._wait_for_completion(attempt)
        self._emit(status_callback, BrowserSignInStatus.EXCHANGING)
        api_key = await self._gateway.exchange(
            attempt.process_id, exchange_token, attempt.code_verifier
        )
        self._emit(status_callback, BrowserSignInStatus.COMPLETED)
        return api_key

    async def authenticate(self, status_callback: StatusCallback | None = None) -> str:
        attempt = await self.start_attempt()
        self._emit(status_callback, BrowserSignInStatus.OPENING_BROWSER)
        self._open_browser_or_raise(attempt.sign_in_url)
        return await self.complete_attempt(attempt, status_callback=status_callback)

    async def _wait_for_completion(self, attempt: BrowserSignInAttempt) -> str:
        consecutive_poll_failures = 0
        while self._now() < attempt.expires_at:
            try:
                payload = await self._gateway.poll(attempt.poll_url)
            except BrowserSignInError as err:
                if err.code is not BrowserSignInErrorCode.POLL_FAILED:
                    raise
                consecutive_poll_failures += 1
                if consecutive_poll_failures >= self._max_consecutive_poll_failures:
                    raise
                await self._sleep_until_next_poll_or_timeout(attempt.expires_at)
                continue

            consecutive_poll_failures = 0
            match payload.status:
                case "pending":
                    await self._sleep_until_next_poll_or_timeout(attempt.expires_at)
                case "completed":
                    if payload.exchange_token:
                        return payload.exchange_token
                    raise BrowserSignInError(
                        "Sign-in worked, but setup couldn't finish.",
                        code=BrowserSignInErrorCode.MISSING_EXCHANGE_TOKEN,
                    )
                case "expired":
                    raise BrowserSignInError(
                        "Browser sign-in expired.", code=BrowserSignInErrorCode.EXPIRED
                    )
                case "denied":
                    raise BrowserSignInError(
                        "Browser sign-in was denied.",
                        code=BrowserSignInErrorCode.DENIED,
                    )
                case "error":
                    raise BrowserSignInError(
                        payload.message or "Browser sign-in failed.",
                        code=BrowserSignInErrorCode.PROVIDER_ERROR,
                    )
                case _:
                    raise BrowserSignInError(
                        "Browser sign-in returned an unknown state.",
                        code=BrowserSignInErrorCode.UNKNOWN_STATE,
                    )

        raise BrowserSignInError(
            "Browser sign-in timed out.", code=BrowserSignInErrorCode.TIMED_OUT
        )

    async def _sleep_until_next_poll_or_timeout(self, expires_at: datetime) -> None:
        remaining_seconds = (expires_at - self._now()).total_seconds()
        if remaining_seconds <= 0:
            raise BrowserSignInError(
                "Browser sign-in timed out.", code=BrowserSignInErrorCode.TIMED_OUT
            )
        await self._sleep(min(self._poll_interval, remaining_seconds))

    def _emit(
        self, callback: StatusCallback | None, status: BrowserSignInStatus
    ) -> None:
        if callback is not None:
            callback(status)

    def _open_browser_or_raise(self, sign_in_url: str) -> None:
        try:
            browser_opened = self._open_browser(sign_in_url)
        except Exception as err:
            raise BrowserSignInError(
                "Failed to open browser for sign-in.",
                code=BrowserSignInErrorCode.OPEN_BROWSER_FAILED,
            ) from err

        if not browser_opened:
            raise BrowserSignInError(
                "Failed to open browser for sign-in.",
                code=BrowserSignInErrorCode.OPEN_BROWSER_FAILED,
            )


def _generate_code_verifier() -> str:
    return secrets.token_urlsafe(64)


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = _generate_code_verifier()
    return verifier, _generate_code_challenge(verifier)


def _generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
