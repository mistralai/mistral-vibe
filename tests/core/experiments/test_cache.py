from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from vibe.core.experiments import cache
from vibe.core.experiments.models import EvalResponse
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE


def _response() -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })


def _config(*, enable_telemetry: bool = True, enable_experiments: bool = True) -> Any:
    config = MagicMock()
    config.enable_telemetry = enable_telemetry
    config.experiments.enable = enable_experiments
    return config


@pytest.fixture
def _fixed_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "_cache_key", lambda _config: "user-abc")


def test_store_then_load_round_trips(_fixed_key: None) -> None:
    cache.store_cached_eval_response(_config(), _response())

    loaded = cache.load_cached_eval_response(_config())

    assert loaded is not None
    assert loaded.features["vibe_cli_system_prompt"].resolved_value() == "cli"


def test_load_returns_none_when_cache_missing(_fixed_key: None) -> None:
    assert cache.load_cached_eval_response(_config()) is None


def test_load_returns_none_when_entry_is_stale(_fixed_key: None) -> None:
    cache.store_cached_eval_response(_config(), _response())

    path = EXPERIMENT_EVAL_CACHE_FILE.path
    entries = json.loads(path.read_text())
    entries["user-abc"]["stored_at_timestamp"] -= cache._EVAL_CACHE_TTL_SECONDS + 1
    path.write_text(json.dumps(entries))

    assert cache.load_cached_eval_response(_config()) is None


def test_load_fails_open_on_corrupt_file(_fixed_key: None) -> None:
    path = EXPERIMENT_EVAL_CACHE_FILE.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json")

    assert cache.load_cached_eval_response(_config()) is None


def test_store_is_noop_when_experiments_disabled() -> None:
    cache.store_cached_eval_response(_config(enable_experiments=False), _response())

    assert not EXPERIMENT_EVAL_CACHE_FILE.path.exists()
    assert cache.load_cached_eval_response(_config(enable_experiments=False)) is None


def test_store_is_noop_when_no_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.telemetry.send.get_mistral_provider_and_api_key",
        lambda _config: None,
    )

    cache.store_cached_eval_response(_config(), _response())

    assert not EXPERIMENT_EVAL_CACHE_FILE.path.exists()


def test_store_and_load_use_hashed_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.core.telemetry.send.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )

    cache.store_cached_eval_response(_config(), _response())
    loaded = cache.load_cached_eval_response(_config())

    assert loaded is not None
    entries = json.loads(EXPERIMENT_EVAL_CACHE_FILE.path.read_text())
    assert "fake-key" not in entries
    assert len(entries) == 1
