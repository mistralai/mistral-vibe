from __future__ import annotations

from contextlib import suppress
from typing import Any

import httpx
import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import LLMMessage, Role
from vibe.core.utils import RetryCategory, RetryReason

_COMPLETION = {
    "id": "cmpl-test",
    "object": "chat.completion",
    "model": "test-model",
    "created": 1,
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"},
        }
    ],
}


class Recorder:
    def __init__(self) -> None:
        self.reasons: list[RetryReason] = []

    async def __call__(self, reason: RetryReason) -> None:
        self.reasons.append(reason)

    @property
    def categories(self) -> list[RetryCategory]:
        return [reason.category for reason in self.reasons]

    @property
    def details(self) -> list[str]:
        return [reason.detail for reason in self.reasons]


def _responder(failures: list[Any]) -> Any:
    """Serve one entry from `failures` per call, then succeed.

    An int entry is served as that status code; an exception entry is raised.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = calls["n"]
        calls["n"] += 1
        if index >= len(failures):
            return httpx.Response(200, json=_COMPLETION)
        failure = failures[index]
        if isinstance(failure, int):
            return httpx.Response(failure, json={"message": "nope"})
        if isinstance(failure, type):
            raise failure("injected", request=request)
        raise failure

    handler.calls = calls  # type: ignore[attr-defined]
    return handler


def _provider(backend: str) -> ProviderConfig:
    return ProviderConfig.model_validate({
        "name": "test-provider",
        "api_base": "http://mock/v1",
        "api_key_env_var": "TEST_RETRY_KEY",
        "backend": backend,
    })


@pytest.fixture(autouse=True)
def _api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_RETRY_KEY", "fake-key")


def _mistral_backend(recorder: Recorder) -> MistralBackend:
    backend = MistralBackend(
        provider=_provider("mistral"),
        timeout=5.0,
        retry_max_elapsed_time=5.0,
        on_retry=recorder,
    )
    # Keep the backoff sub-millisecond: this asserts on observation, not timing.
    backend._retry_config.backoff.initial_interval = 1
    backend._retry_config.backoff.max_interval = 2
    backend._retry_config.backoff.exponent = 1.0
    return backend


async def _complete(backend: Any) -> None:
    await backend.complete(
        model=_model(),
        messages=[LLMMessage(role=Role.user, content="hi")],
        temperature=0.0,
        tools=None,
        max_tokens=None,
        tool_choice=None,
        extra_headers=None,
    )


def _model() -> Any:
    from vibe.core.config import ModelConfig

    return ModelConfig.model_validate({
        "name": "test-model",
        "provider": "test-provider",
        "alias": "test-model",
    })


@pytest.mark.asyncio
async def test_mistral_reports_retryable_status_before_the_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    handler = _responder([429, 503])
    backend = _mistral_backend(recorder)

    _patch_client_transport(monkeypatch, httpx.MockTransport(handler))
    async with backend:
        await _complete(backend)

    assert handler.calls["n"] == 3
    assert recorder.details == ["HTTP 429", "HTTP 503"]
    assert recorder.categories == [
        RetryCategory.RATE_LIMITED,
        RetryCategory.SERVER_ERROR,
    ]


@pytest.mark.asyncio
async def test_mistral_reports_connection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    request = httpx.Request("POST", "http://mock/v1/chat/completions")
    handler = _responder([httpx.ReadError("reset", request=request)])
    backend = _mistral_backend(recorder)

    _patch_client_transport(monkeypatch, httpx.MockTransport(handler))
    async with backend:
        await _complete(backend)

    assert handler.calls["n"] == 2
    assert recorder.details == ["ReadError"]
    assert recorder.categories == [RetryCategory.CONNECTION]


@pytest.mark.asyncio
async def test_mistral_stays_silent_when_nothing_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = Recorder()
    handler = _responder([])
    backend = _mistral_backend(recorder)

    _patch_client_transport(monkeypatch, httpx.MockTransport(handler))
    async with backend:
        await _complete(backend)

    assert handler.calls["n"] == 1
    assert recorder.reasons == []


@pytest.mark.parametrize(
    "failure",
    [429, 500, 502, 503, 504, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout],
)
@pytest.mark.asyncio
async def test_reported_failures_are_the_ones_the_sdk_actually_retries(
    monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    recorder = Recorder()
    handler = _responder([failure])
    backend = _mistral_backend(recorder)

    _patch_client_transport(monkeypatch, httpx.MockTransport(handler))
    async with backend:
        await _complete(backend)

    assert handler.calls["n"] == 2
    assert len(recorder.reasons) == 1


@pytest.mark.parametrize("failure", [400, 401, 404, 422, httpx.RemoteProtocolError])
@pytest.mark.asyncio
async def test_failures_the_sdk_never_retries_are_not_reported(
    monkeypatch: pytest.MonkeyPatch, failure: Any
) -> None:
    recorder = Recorder()
    handler = _responder([failure])
    backend = _mistral_backend(recorder)

    _patch_client_transport(monkeypatch, httpx.MockTransport(handler))
    async with backend:
        with suppress(Exception):
            await _complete(backend)

    assert handler.calls["n"] == 1
    assert recorder.reasons == []


@pytest.mark.asyncio
async def test_generic_backend_reports_retries_the_same_way() -> None:
    recorder = Recorder()
    handler = _responder([429, 503])
    client = _vibe_client(httpx.MockTransport(handler))

    backend = GenericBackend(
        client=client, provider=_provider("generic"), on_retry=recorder
    )
    async with backend:
        await _complete(backend)

    assert handler.calls["n"] == 3
    assert recorder.details == ["HTTP 429", "HTTP 503"]
    assert recorder.categories == [
        RetryCategory.RATE_LIMITED,
        RetryCategory.SERVER_ERROR,
    ]


@pytest.mark.asyncio
async def test_generic_backend_paces_subsequent_calls_after_a_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 429 should make the *next* call wait; successes before any 429 never wait."""
    from vibe.core.utils import retry as retry_module
    from vibe.core.utils.pacing import AdaptivePacer

    # First call 429s (retried to success), all later calls succeed. We assert
    # only later calls pay a pacing wait, using a fast pacer base interval.
    handler = _responder([429])
    client = _vibe_client(httpx.MockTransport(handler))

    now = 0.0
    sleeps: list[float] = []

    def fake_clock() -> float:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    async def noop_sleep(seconds: float) -> None:  # for the retry backoff only
        pass

    pacer = AdaptivePacer(
        base_interval_seconds=0.01,
        recovery_window_seconds=60.0,
        clock=fake_clock,
        sleep=fake_sleep,
    )
    backend = GenericBackend(
        client=client, provider=_provider("generic"), on_retry=Recorder(), pacer=pacer
    )

    # Keep the retry backoff itself from sleeping so pacing is the only wait.
    monkeypatch.setattr(retry_module.asyncio, "sleep", noop_sleep)

    async with backend:
        await _complete(backend)  # call 1: 429 then success -> sets pace
        await _complete(backend)  # call 2: should pay the pacing wait
        await _complete(backend)  # call 3: success, pace still active

    waits = [s for s in sleeps if s > 0]
    # The pacing wait (~0.01s) appears on calls 2 and 3, not call 1.
    assert len(waits) >= 2
    assert all(w == pytest.approx(0.01, abs=0.01) for w in waits)


def _vibe_client(transport: httpx.MockTransport) -> Any:
    from vibe.utils.http import VibeAsyncHTTPClient

    return VibeAsyncHTTPClient(transport=transport)


def _patch_client_transport(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    """Force the Mistral backend's own client onto a mock transport.

    Patched rather than injected because the backend constructs the client
    itself -- that construction is what registers both seams, so it is the thing
    under test.
    """
    from vibe.utils import http as http_module

    original = http_module.VibeAsyncHTTPClient

    def factory(**kwargs: Any) -> Any:
        kwargs["transport"] = transport
        kwargs.pop("verify", None)
        return original(**kwargs)

    monkeypatch.setattr("vibe.core.llm.backend.mistral.VibeAsyncHTTPClient", factory)
