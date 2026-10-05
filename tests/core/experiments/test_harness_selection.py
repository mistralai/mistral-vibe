from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from vibe._experimental_harness import (
    ExperimentalHarnessUnavailableError,
    create_experimental_harness_host,
    require_experimental_harness,
    resolve_harness_selection,
)
from vibe.core.experiments.models import EvalResponse, FeatureDefinition, FeatureRule
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE


def _cached_response(variant: str | None) -> EvalResponse:
    return EvalResponse(
        features={
            "vibe_cli_unified_harness_rollout": FeatureDefinition(
                defaultValue="legacy", rules=[FeatureRule(force=variant)]
            )
        }
    )


def _write_rollout_cache(variant: str, *, expired: bool = False) -> None:
    """Write an eval-cache entry carrying a rollout assignment.

    Used to prove that a stale GrowthBook rollout — the mechanism the hard
    default replaces — can no longer influence harness selection.
    """
    import json

    stored_at = int(time.time()) - (10 * 7 * 24 * 60 * 60 if expired else 60)
    EXPERIMENT_EVAL_CACHE_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENT_EVAL_CACHE_FILE.path.write_text(
        json.dumps({
            "some-hashed-key": {
                "stored_at_timestamp": stored_at,
                "payload": _cached_response(variant).model_dump(mode="json"),
            }
        }),
        encoding="utf-8",
    )


class TestResolveHarnessSelection:
    def test_no_flags_defaults_to_unified(self) -> None:
        selection = resolve_harness_selection(
            experimental_harness=False, legacy_harness=False
        )
        assert selection.use_unified is True
        assert selection.source == "default"

    def test_experimental_harness_flag_selects_unified(self) -> None:
        selection = resolve_harness_selection(
            experimental_harness=True, legacy_harness=False
        )
        assert selection.use_unified is True
        assert selection.source == "flag"

    def test_legacy_harness_flag_selects_legacy(self) -> None:
        selection = resolve_harness_selection(
            experimental_harness=False, legacy_harness=True
        )
        assert selection.use_unified is False
        assert selection.source == "flag-legacy"

    def test_legacy_harness_flag_wins_over_experimental_harness(self) -> None:
        selection = resolve_harness_selection(
            experimental_harness=True, legacy_harness=True
        )
        assert selection.use_unified is False
        assert selection.source == "flag-legacy"

    def test_rollout_cache_content_cannot_select_legacy(self) -> None:
        """A rollout cache entry (the pre-hard-default mechanism) must not
        reach the selection: the default is unified whether the cache is
        missing, fresh, or carries a legacy assignment.
        """
        _write_rollout_cache("legacy")
        selection = resolve_harness_selection(
            experimental_harness=False, legacy_harness=False
        )
        assert selection.use_unified is True
        assert selection.source == "default"

    def test_expired_rollout_cache_behaves_like_cache_miss(self) -> None:
        _write_rollout_cache("legacy", expired=True)
        selection = resolve_harness_selection(
            experimental_harness=False, legacy_harness=False
        )
        assert selection.use_unified is True
        assert selection.source == "default"


class TestRequireExperimentalHarness:
    def test_passes_when_the_runtime_loads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: True)
        monkeypatch.setattr(
            mod,
            "create_experimental_harness_host",
            lambda: SimpleNamespace(configure_hook_handlers=lambda _handlers: None),
        )
        require_experimental_harness()

    def test_missing_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: False)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            require_experimental_harness()
        message = str(excinfo.value)
        assert "not installed" in message
        assert "--legacy-harness" in message
        assert "uv tool upgrade mistral-vibe" in message

    def test_incompatible_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pinned harness whose Host lacks the builtin-hook registry must
        abort the pre-serving probe: ACP cannot surface a mid-session
        failure as a process abort, so the probe covers this case.
        """
        import vibe._experimental_harness as mod

        monkeypatch.setattr(mod, "experimental_harness_available", lambda: True)
        monkeypatch.setattr(
            mod, "create_experimental_harness_host", lambda: SimpleNamespace()
        )
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            require_experimental_harness()
        message = str(excinfo.value)
        assert "configure_hook_handlers" in message
        assert "--legacy-harness" in message


class TestCreateExperimentalHarnessHost:
    def test_import_failure_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _missing_module(name: str) -> object:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)

        monkeypatch.setattr("vibe._experimental_harness.import_module", _missing_module)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        assert "could not be loaded" in str(excinfo.value)
        assert "--legacy-harness" in str(excinfo.value)

    def test_incompatible_runtime_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        def _old_runtime(name: str) -> object:
            # A harness version without the create_harness_host factory.
            return types.SimpleNamespace()

        monkeypatch.setattr("vibe._experimental_harness.import_module", _old_runtime)
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        assert "AttributeError" in str(excinfo.value)

    def test_initialization_failure_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import types

        def _failing_runtime(name: str) -> object:
            module = types.SimpleNamespace(
                create_harness_host=lambda: (_ for _ in ()).throw(
                    RuntimeError("native extension incompatible")
                )
            )
            return module

        monkeypatch.setattr(
            "vibe._experimental_harness.import_module", _failing_runtime
        )
        with pytest.raises(ExperimentalHarnessUnavailableError) as excinfo:
            create_experimental_harness_host()
        message = str(excinfo.value)
        assert "initialization failed" in message
        assert "native extension incompatible" in message
        assert "--legacy-harness" in message
