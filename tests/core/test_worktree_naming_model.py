from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from vibe.core.config.models import ProviderConfig
from vibe.core.git.worktree import naming_model as worktree_naming_model
from vibe.core.git.worktree.naming_model import suggest_worktree_name


@dataclass
class _Message:
    content: str | None


@dataclass
class _Completion:
    message: _Message


class _FakeBackend:
    def __init__(self, *, answer: str | None = None, error: Exception | None = None):
        self._answer = answer
        self._error = error
        self.closed = False

    async def __aenter__(self) -> _FakeBackend:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.closed = True

    async def complete(self, **_kwargs: Any) -> _Completion:
        if self._error is not None:
            raise self._error
        return _Completion(_Message(self._answer))


class _SlowBackend(_FakeBackend):
    async def complete(self, **_kwargs: Any) -> _Completion:
        await asyncio.sleep(10)
        raise AssertionError("should have timed out")


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    providers: list[ProviderConfig],
    backend: _FakeBackend | None = None,
    api_key: str | None = "key",
) -> None:
    class _Config:
        def __init__(self) -> None:
            self.providers = providers

    class _Orchestrator:
        def __init__(self) -> None:
            self.config = _Config()

    async def build(**_kwargs: Any) -> _Orchestrator:
        return _Orchestrator()

    monkeypatch.setattr(worktree_naming_model, "build_default_orchestrator", build)
    monkeypatch.setattr(worktree_naming_model, "resolve_api_key", lambda _: api_key)
    monkeypatch.setattr(
        worktree_naming_model, "create_backend", lambda **_: backend or _FakeBackend()
    )


def _mistral() -> ProviderConfig:
    return ProviderConfig(
        name="mistral", api_base="https://example.invalid", api_key_env_var="KEY"
    )


async def _suggest() -> str | None:
    return await suggest_worktree_name("Fix the login bug", cwd=Path.cwd())


@pytest.mark.asyncio
async def test_returns_the_model_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        providers=[_mistral()],
        backend=_FakeBackend(answer="fix-login-bug"),
    )

    assert await _suggest() == "fix-login-bug"


@pytest.mark.asyncio
async def test_returns_none_without_a_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = ProviderConfig(name="other", api_base="https://example.invalid")
    _install(monkeypatch, providers=[other])

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_without_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, providers=[_mistral()], api_key=None)

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_when_the_model_answers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, providers=[_mistral()], backend=_FakeBackend(answer=""))

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_when_the_backend_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        providers=[_mistral()],
        backend=_FakeBackend(error=RuntimeError("connection reset")),
    )

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_when_the_model_is_too_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worktree_naming_model, "_TOTAL_TIMEOUT_SECONDS", 0.01)
    _install(monkeypatch, providers=[_mistral()], backend=_SlowBackend())

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_does_not_call_the_model_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(**_kwargs: Any) -> None:
        raise AssertionError("no config should be loaded without a prompt")

    monkeypatch.setattr(worktree_naming_model, "build_default_orchestrator", explode)

    assert await suggest_worktree_name("", cwd=Path.cwd()) is None
