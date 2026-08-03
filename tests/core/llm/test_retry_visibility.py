from __future__ import annotations

from contextlib import suppress
from typing import Any

import httpx
import pytest

from vibe.core.config import ProviderConfig
from vibe.core.llm.backend.generic import GenericBackend
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import LLMMessage, Role

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
        self.reasons: list[str] = []

    async def __call__(self, reason: str) -> None:
        self.reasons.append(reason)


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
    assert recorder.reasons == ["HTTP 429", "HTTP 503"]


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
    assert recorder.reasons == ["ReadError"]


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
    assert recorder.reasons == ["HTTP 429", "HTTP 503"]


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
