"""The availability probe: what it asks, what it remembers, and for how long."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.llm import model_probe
from vibe.core.llm.exceptions import BackendError, PayloadSummary
from vibe.core.llm.model_probe import CompletionProbeSource, ModelAvailabilityCache
from vibe.core.paths import UTILITY_MODEL_CACHE_FILE
from vibe.core.types import Backend

_MODEL = ModelConfig(name="mistral-small-latest", provider="mistral", alias="small")
_PREFERRED = ModelConfig(name="mistral-vibe-cli-fast", provider="mistral", alias="fast")

# Captured before the conftest fixture stubs it out.
_REAL_PROBE = model_probe._probe


def _provider(api_base: str = "https://llm.acme.internal/v1") -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base=api_base,
        api_key_env_var="MISTRAL_API_KEY",
        backend=Backend.MISTRAL,
    )


def _stub_probe(
    monkeypatch: pytest.MonkeyPatch, verdicts: list[bool | None]
) -> list[str]:
    """Replace the network step with a scripted sequence; records each call."""
    calls: list[str] = []

    async def fake_probe(*, provider: Any, model: Any, timeout_seconds: float):
        calls.append(model.name)
        return verdicts.pop(0)

    monkeypatch.setattr(model_probe, "_probe", fake_probe)
    return calls


class _RecordingSource:
    """A source scripted with one verdict map, recording what it was asked."""

    def __init__(self, verdicts: dict[str, bool]) -> None:
        self.verdicts = verdicts
        self.asked: list[list[str]] = []

    async def check(self, *, provider: Any, models: Any, timeout_seconds: float):
        self.asked.append([model.name for model in models])
        return {name: v for name, v in self.verdicts.items() if name in self.asked[-1]}


class TestEnsureFirstAvailable:
    @pytest.mark.asyncio
    async def test_asks_once_and_then_serves_the_answer(self) -> None:
        source = _RecordingSource({_MODEL.name: True})
        cache = ModelAvailabilityCache(source)

        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])
        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])

        assert cache.peek(provider=_provider(), model=_MODEL) is True
        assert source.asked == [[_MODEL.name]]

    @pytest.mark.asyncio
    async def test_a_source_answering_many_models_at_once_is_one_request(self) -> None:
        source = _RecordingSource({_PREFERRED.name: False, _MODEL.name: True})
        cache = ModelAvailabilityCache(source)

        await cache.ensure_first_available(
            provider=_provider(), models=[_PREFERRED, _MODEL]
        )

        assert source.asked == [[_PREFERRED.name, _MODEL.name]]
        assert cache.peek(provider=_provider(), model=_PREFERRED) is False
        assert cache.peek(provider=_provider(), model=_MODEL) is True

    @pytest.mark.asyncio
    async def test_models_after_a_known_available_one_are_not_asked_about(self) -> None:
        source = _RecordingSource({})
        cache = ModelAvailabilityCache(source)
        cache.remember(provider=_provider(), model=_PREFERRED, available=True)

        await cache.ensure_first_available(
            provider=_provider(), models=[_PREFERRED, _MODEL]
        )

        assert source.asked == []

    @pytest.mark.asyncio
    async def test_an_undecided_model_never_reaches_the_disk(self) -> None:
        cache = ModelAvailabilityCache(_RecordingSource({}))

        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])

        assert cache.peek(provider=_provider(), model=_MODEL) is None
        assert not UTILITY_MODEL_CACHE_FILE.path.exists()

    @pytest.mark.asyncio
    async def test_a_missing_key_is_not_a_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        source = _RecordingSource({_MODEL.name: True})
        cache = ModelAvailabilityCache(source)

        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])

        assert source.asked == []
        assert cache.peek(provider=_provider(), model=_MODEL) is None

    @pytest.mark.asyncio
    async def test_concurrent_opens_ask_once(self) -> None:
        import asyncio

        source = _RecordingSource({_MODEL.name: True})
        cache = ModelAvailabilityCache(source)

        await asyncio.gather(*[
            cache.ensure_first_available(provider=_provider(), models=[_MODEL])
            for _ in range(4)
        ])

        assert source.asked == [[_MODEL.name]]


class TestCompletionProbeSource:
    @pytest.mark.asyncio
    async def test_stops_at_the_first_model_that_answers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _stub_probe(monkeypatch, [True])

        verdicts = await CompletionProbeSource().check(
            provider=_provider(), models=[_PREFERRED, _MODEL], timeout_seconds=1.0
        )

        assert verdicts == {_PREFERRED.name: True}
        assert calls == [_PREFERRED.name]

    @pytest.mark.asyncio
    async def test_tries_the_next_model_when_one_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _stub_probe(monkeypatch, [False, True])

        verdicts = await CompletionProbeSource().check(
            provider=_provider(), models=[_PREFERRED, _MODEL], timeout_seconds=1.0
        )

        assert verdicts == {_PREFERRED.name: False, _MODEL.name: True}
        assert calls == [_PREFERRED.name, _MODEL.name]

    @pytest.mark.asyncio
    async def test_a_timeout_leaves_the_model_out(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_probe(monkeypatch, [None, True])

        verdicts = await CompletionProbeSource().check(
            provider=_provider(), models=[_PREFERRED, _MODEL], timeout_seconds=1.0
        )

        assert verdicts == {_MODEL.name: True}


class TestRemembering:
    def test_a_verdict_survives_a_new_process(self) -> None:
        ModelAvailabilityCache().remember(
            provider=_provider(), model=_MODEL, available=True
        )

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is True

    def test_a_trailing_slash_is_the_same_endpoint(self) -> None:
        ModelAvailabilityCache().remember(
            provider=_provider("https://llm.acme.internal/v1"),
            model=_MODEL,
            available=True,
        )

        verdict = ModelAvailabilityCache().peek(
            provider=_provider("https://llm.acme.internal/v1/"), model=_MODEL
        )

        assert verdict is True

    def test_verdicts_are_per_endpoint(self) -> None:
        cache = ModelAvailabilityCache()
        cache.remember(provider=_provider(), model=_MODEL, available=True)

        other = cache.peek(
            provider=_provider("https://llm.other.internal/v1"), model=_MODEL
        )

        assert other is None

    def test_verdicts_are_per_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = ModelAvailabilityCache()
        monkeypatch.setenv("MISTRAL_API_KEY", "first-key")
        cache.remember(provider=_provider(), model=_MODEL, available=True)

        monkeypatch.setenv("MISTRAL_API_KEY", "second-key")

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is None

    def test_the_file_names_neither_the_endpoint_nor_the_key(self) -> None:
        ModelAvailabilityCache().remember(
            provider=_provider(), model=_MODEL, available=True
        )

        raw = UTILITY_MODEL_CACHE_FILE.path.read_text(encoding="utf-8")

        assert "acme" not in raw
        assert "mock" not in raw

    def test_a_stale_success_is_re_probed(self) -> None:
        cache = ModelAvailabilityCache()
        cache.remember(provider=_provider(), model=_MODEL, available=True)
        _age_entries(days=8)

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is None

    def test_a_success_is_believed_for_the_week(self) -> None:
        cache = ModelAvailabilityCache()
        cache.remember(provider=_provider(), model=_MODEL, available=True)
        _age_entries(days=6)

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is True

    def test_a_failure_expires_within_the_hour(self) -> None:
        cache = ModelAvailabilityCache()
        cache.remember(provider=_provider(), model=_MODEL, available=False)
        _age_entries(hours=2)

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is None

    def test_a_corrupt_cache_file_reads_as_unknown(self) -> None:
        UTILITY_MODEL_CACHE_FILE.path.parent.mkdir(parents=True, exist_ok=True)
        UTILITY_MODEL_CACHE_FILE.path.write_text("{not json", encoding="utf-8")

        assert ModelAvailabilityCache().peek(provider=_provider(), model=_MODEL) is None


class TestProbeVerdicts:
    @pytest.mark.asyncio
    async def test_a_completion_means_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.mock.utils import mock_llm_chunk
        from tests.stubs.fake_backend import FakeBackend

        backend = FakeBackend([mock_llm_chunk(content="pong")])
        monkeypatch.setattr(
            "vibe.core.llm.backend.factory.create_backend", lambda **_: backend
        )

        assert (
            await _REAL_PROBE(provider=_provider(), model=_MODEL, timeout_seconds=1.0)
            is True
        )
        metadata = backend._requests_metadata[0]
        assert metadata is not None
        assert metadata["call_type"] == "secondary_call"

    @pytest.mark.parametrize(
        ("status", "verdict"),
        [
            # refusals
            (400, False),
            (401, False),
            (403, False),
            (404, False),
            (422, False),
            # inconclusive
            (408, None),
            (429, None),
            (500, None),
            (503, None),
            (None, None),
        ],
    )
    @pytest.mark.asyncio
    async def test_only_a_client_error_is_a_refusal(
        self, monkeypatch: pytest.MonkeyPatch, status: int | None, verdict: bool | None
    ) -> None:
        from tests.stubs.fake_backend import FakeBackend

        monkeypatch.setattr(
            "vibe.core.llm.backend.factory.create_backend",
            lambda **_: FakeBackend(exception_to_raise=_backend_error(status)),
        )

        assert (
            await _REAL_PROBE(provider=_provider(), model=_MODEL, timeout_seconds=1.0)
            is verdict
        )

    @pytest.mark.asyncio
    async def test_an_unexpected_error_is_no_verdict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.stubs.fake_backend import FakeBackend

        monkeypatch.setattr(
            "vibe.core.llm.backend.factory.create_backend",
            lambda **_: FakeBackend(exception_to_raise=RuntimeError("boom")),
        )

        assert (
            await _REAL_PROBE(provider=_provider(), model=_MODEL, timeout_seconds=1.0)
            is None
        )


class TestBudget:
    @pytest.mark.asyncio
    async def test_the_budget_covers_the_whole_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Losing the early refusal would let the resolver treat that model as usable.
        given: list[float] = []

        async def slow_refusal(*, provider: Any, model: Any, timeout_seconds: float):
            given.append(timeout_seconds)
            await asyncio.sleep(0.2)
            return False

        monkeypatch.setattr(model_probe, "_probe", slow_refusal)
        third = ModelConfig(name="third", provider="mistral", alias="third")

        verdicts = await CompletionProbeSource().check(
            provider=_provider(),
            models=[_PREFERRED, _MODEL, third],
            timeout_seconds=0.3,
        )

        assert verdicts == {_PREFERRED.name: False, _MODEL.name: False}
        assert given[0] == pytest.approx(0.3, abs=0.05)
        assert given[1] < 0.15  # what was left, not a fresh budget
        assert len(given) == 2


class TestInProcessMemory:
    @pytest.mark.asyncio
    async def test_an_undecided_model_is_not_re_asked_by_the_same_process(self) -> None:
        source = _RecordingSource({})
        cache = ModelAvailabilityCache(source)

        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])
        await cache.ensure_first_available(provider=_provider(), models=[_MODEL])
        await ModelAvailabilityCache(source).ensure_first_available(
            provider=_provider(), models=[_MODEL]
        )

        assert len(source.asked) == 2

    def test_a_miss_does_not_re_read_the_file_every_time(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads: list[str] = []
        real_read = model_probe._read_entry

        def counting_read(key: str):
            reads.append(key)
            return real_read(key)

        monkeypatch.setattr(model_probe, "_read_entry", counting_read)
        cache = ModelAvailabilityCache()

        for _ in range(5):
            assert cache.peek(provider=_provider(), model=_MODEL) is None

        assert len(reads) == 1

    def test_a_verdict_clears_a_remembered_miss(self) -> None:
        cache = ModelAvailabilityCache()
        assert cache.peek(provider=_provider(), model=_MODEL) is None

        cache.remember(provider=_provider(), model=_MODEL, available=True)

        assert cache.peek(provider=_provider(), model=_MODEL) is True

    def test_reset_forgets_memory_but_not_the_file(self) -> None:
        cache = ModelAvailabilityCache()
        cache.remember(provider=_provider(), model=_MODEL, available=True)

        cache.reset()

        assert cache.peek(provider=_provider(), model=_MODEL) is True


def _backend_error(status: int | None) -> BackendError:
    return BackendError(
        provider="mistral",
        endpoint="https://llm.acme.internal/v1",
        status=status,
        reason=None,
        headers=None,
        body_text=None,
        parsed_error=None,
        model=_MODEL.name,
        payload_summary=PayloadSummary(
            model=_MODEL.name,
            message_count=1,
            approx_chars=4,
            temperature=0.0,
            has_tools=False,
            tool_choice=None,
        ),
    )


def _age_entries(*, days: int = 0, hours: int = 0) -> None:
    """Backdate every stored verdict, as elapsed time would."""
    path = UTILITY_MODEL_CACHE_FILE.path
    entries: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    shift = days * 24 * 60 * 60 + hours * 60 * 60
    for entry in entries.values():
        entry["stored_at_timestamp"] = int(time.time()) - shift
    path.write_text(json.dumps(entries), encoding="utf-8")
