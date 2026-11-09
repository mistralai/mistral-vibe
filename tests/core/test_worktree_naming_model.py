from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vibe.core.git.worktree import naming_model as worktree_naming_model
from vibe.core.git.worktree.naming_model import suggest_worktree_name


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    answer: str | None = None,
    error: Exception | None = None,
    slow: bool = False,
) -> None:
    class _Orchestrator:
        config = object()

    async def build(**_kwargs: Any) -> _Orchestrator:
        return _Orchestrator()

    async def complete(**_kwargs: Any) -> str | None:
        if slow:
            await asyncio.sleep(10)
        if error is not None:
            raise error
        return answer

    monkeypatch.setattr(worktree_naming_model, "build_default_orchestrator", build)
    monkeypatch.setattr(worktree_naming_model, "run_utility_completion", complete)


async def _suggest() -> str | None:
    return await suggest_worktree_name("Fix the login bug", cwd=Path.cwd())


@pytest.mark.asyncio
async def test_returns_the_model_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, answer="fix-login-bug")

    assert await _suggest() == "fix-login-bug"


@pytest.mark.asyncio
async def test_returns_none_when_the_model_answers_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, answer="")

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_when_the_completion_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, error=RuntimeError("connection reset"))

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_returns_none_when_the_model_is_too_slow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worktree_naming_model, "_TOTAL_TIMEOUT_SECONDS", 0.01)
    _install(monkeypatch, slow=True)

    assert await _suggest() is None


@pytest.mark.asyncio
async def test_opts_into_the_no_key_fast_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Orchestrator:
        config = object()

    async def build(**_kwargs: Any) -> _Orchestrator:
        return _Orchestrator()

    async def complete(**kwargs: Any) -> str | None:
        captured.update(kwargs)
        return "fix-login-bug"

    monkeypatch.setattr(worktree_naming_model, "build_default_orchestrator", build)
    monkeypatch.setattr(worktree_naming_model, "run_utility_completion", complete)

    await _suggest()

    # Naming runs at session start with a deterministic fallback ready, so a
    # keyless provider must short-circuit instead of waiting out the budget.
    assert captured["skip_if_no_key"] is True


@pytest.mark.asyncio
async def test_does_not_load_config_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode(**_kwargs: Any) -> None:
        raise AssertionError("no config should be loaded without a prompt")

    monkeypatch.setattr(worktree_naming_model, "build_default_orchestrator", explode)

    assert await suggest_worktree_name("", cwd=Path.cwd()) is None
