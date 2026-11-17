"""Waiting for a provider credential the Host could not check yet.

A Host answers ``ProviderAuthRequired`` with reason ``unavailable`` when the
owner of the credential cannot answer right now, typically because the
computer is asleep or has just woken and has no network yet. Nothing says the
credential is bad, so the model call waits for it, with backoff, before the
turn fails. Any other answer ends the wait at once: a ready credential is
used, and every other reason fails the call as it always has.

The wait has its own budget, separate from the provider retry budget: no
request has been sent yet, so there is nothing for the provider to retry.

The budget counts the time the computer is awake. It is measured with
``time.monotonic``, which is also the clock the event loop's timers use. On
macOS that clock is ``mach_absolute_time``, which does not advance while the
system sleeps (``clock_gettime(3)``: ``CLOCK_UPTIME_RAW`` "does not increment
while the system is asleep" and is "identical to the result of
mach_absolute_time()"). On Linux it is ``CLOCK_MONOTONIC``, which does not
advance during suspend either. A computer that sleeps through the wait
therefore resumes it on waking instead of finding it spent. On Windows the
clock is ``QueryPerformanceCounter``, which does count time asleep, so there a
long sleep can spend the wait.
"""

from __future__ import annotations

import asyncio
from asyncio import sleep
import logging
from random import uniform
import time

from mistralai_vibe_local_harness.vibe._credentials import (
    ProviderAuthRequired,
    ProviderCredentialProvider,
    ProviderCredentialResult,
)
from mistralai_vibe_local_harness.vibe._runtime_config import (
    ProviderRetry,
    ProviderRetryObserver,
)

logger = logging.getLogger(__name__)

# Shown with the retry notice, as a provider retry shows its HTTP status.
CREDENTIAL_WAIT_DETAIL = "Sign-in not checked yet"

_INITIAL_DELAY_S = 1.0
_BACKOFF = 2.0
_MAX_DELAY_S = 30.0
_MAX_JITTER_S = 1.0
# The time left for a check: the last wait ends this much before the budget,
# so the check it waited for has time to answer.
_CHECK_S = 5.0
# The delay reaches its maximum long before this exponent; the cap keeps a
# large budget from overflowing the power.
_MAX_BACKOFF_EXPONENT = 16


async def resolve_credential(
    credentials: ProviderCredentialProvider,
    *,
    wait_s: float,
    notices: ProviderRetryObserver,
) -> ProviderCredentialResult:
    """Resolve the credential, waiting up to ``wait_s`` awake seconds while
    the Host reports it ``unavailable``.

    Each wait is announced through ``notices`` with the ``connection``
    category, and the notice is withdrawn once the wait ends, whatever ends
    it. Returns the last answer: a snapshot, another reason, or still
    ``unavailable`` once the budget is spent. A cancellation during the wait
    propagates at once.

    The first check takes as long as the Host does, and the budget starts once
    it answers ``unavailable``. A later check is cut off when
    the budget runs out, and counts as still ``unavailable``: a Host that
    stopped answering after a wake must not hold the turn past its budget. The
    waits stop ``_CHECK_S`` before the budget, so the last check has that long
    to answer, and a budget shorter than that does not wait at all.
    """
    credential = await credentials.resolve()
    if not _unavailable(credential):
        return credential
    # The budget starts with the first ``unavailable`` answer: a ready
    # credential, the common case, reads no clock.
    started = time.monotonic()
    attempt = 0
    announced = False
    try:
        while True:
            remaining_s = wait_s - (time.monotonic() - started)
            # Within the last ``_CHECK_S``, the check just made was the last.
            if not _unavailable(credential) or remaining_s <= _CHECK_S:
                break
            attempt += 1
            delay_s = min(_backoff_delay_s(attempt), remaining_s - _CHECK_S)
            logger.info(
                "Provider credential unavailable; checking again in %.1fs (attempt %d)",
                delay_s,
                attempt,
            )
            await notices(
                ProviderRetry(
                    category="connection",
                    detail=CREDENTIAL_WAIT_DETAIL,
                    delay_s=delay_s,
                    retry_attempt=attempt,
                )
            )
            announced = True
            await sleep(delay_s)
            credential = await _check_again(
                credentials, credential, timeout_s=wait_s - (time.monotonic() - started)
            )
    finally:
        if announced:
            await notices(None)
    if _unavailable(credential) and attempt > 0:
        logger.warning(
            "Provider credential still unavailable after %d checks over %.1fs awake",
            attempt + 1,
            time.monotonic() - started,
        )
    return credential


async def _check_again(
    credentials: ProviderCredentialProvider,
    unavailable: ProviderCredentialResult,
    *,
    timeout_s: float,
) -> ProviderCredentialResult:
    """Ask again, keeping the last ``unavailable`` answer when the Host does
    not answer within ``timeout_s``.
    """
    scope = asyncio.timeout(max(timeout_s, 0.0))
    try:
        async with scope:
            return await credentials.resolve()
    except TimeoutError:
        if not scope.expired():
            raise
        logger.info("Provider credential check did not answer before the budget")
        return unavailable


def _unavailable(credential: ProviderCredentialResult) -> bool:
    return (
        isinstance(credential, ProviderAuthRequired)
        and credential.reason == "unavailable"
    )


def _backoff_delay_s(attempt: int) -> float:
    exponent = min(attempt - 1, _MAX_BACKOFF_EXPONENT)
    delay_s = _INITIAL_DELAY_S * _BACKOFF**exponent
    return min(delay_s + uniform(0, _MAX_JITTER_S), _MAX_DELAY_S)


__all__ = ["CREDENTIAL_WAIT_DETAIL", "resolve_credential"]
