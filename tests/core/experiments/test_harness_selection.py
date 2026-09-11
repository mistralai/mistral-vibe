from __future__ import annotations

import pytest

from vibe._experimental_harness import resolve_harness_selection
from vibe.core.experiments.models import EvalResponse, FeatureDefinition, FeatureRule


def _cached_response(variant: str | None) -> EvalResponse | None:
    if variant is None:
        return None
    return EvalResponse(
        features={
            "vibe_cli_unified_harness_rollout": FeatureDefinition(
                defaultValue="legacy", rules=[FeatureRule(force=variant)]
            )
        }
    )


@pytest.fixture(autouse=True)
def _harness_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unified harness package is not installed in CI, so force
    availability for tests that expect unified selection.
    """
    import vibe._experimental_harness as mod

    monkeypatch.setattr(mod, "experimental_harness_available", lambda: True)


def test_no_flags_no_cache_defaults_to_legacy() -> None:
    selection = resolve_harness_selection(
        experimental_harness=False, legacy_harness=False, cached_eval=None
    )
    assert selection.use_unified is False
    assert selection.source == "default"


def test_cache_with_unified_selects_unified() -> None:
    selection = resolve_harness_selection(
        experimental_harness=False,
        legacy_harness=False,
        cached_eval=_cached_response("unified"),
    )
    assert selection.use_unified is True
    assert selection.source == "rollout"


def test_cache_with_legacy_selects_legacy() -> None:
    selection = resolve_harness_selection(
        experimental_harness=False,
        legacy_harness=False,
        cached_eval=_cached_response("legacy"),
    )
    assert selection.use_unified is False
    assert selection.source == "default"


def test_legacy_harness_flag_wins_over_rollout_cache() -> None:
    selection = resolve_harness_selection(
        experimental_harness=False,
        legacy_harness=True,
        cached_eval=_cached_response("unified"),
    )
    assert selection.use_unified is False
    assert selection.source == "flag-legacy"


def test_experimental_harness_flag_wins_over_rollout_cache() -> None:
    selection = resolve_harness_selection(
        experimental_harness=True,
        legacy_harness=False,
        cached_eval=_cached_response("legacy"),
    )
    assert selection.use_unified is True
    assert selection.source == "flag"


def test_experimental_harness_flag_wins_over_no_cache() -> None:
    selection = resolve_harness_selection(
        experimental_harness=True, legacy_harness=False, cached_eval=None
    )
    assert selection.use_unified is True
    assert selection.source == "flag"


def test_legacy_harness_wins_over_experimental_harness() -> None:
    selection = resolve_harness_selection(
        experimental_harness=True, legacy_harness=True, cached_eval=None
    )
    assert selection.use_unified is False
    assert selection.source == "flag-legacy"


def test_cache_without_rollout_feature_returns_default() -> None:
    cached = EvalResponse(
        features={
            "vibe_cli_system_prompt": FeatureDefinition(defaultValue="cli", rules=[])
        }
    )
    selection = resolve_harness_selection(
        experimental_harness=False, legacy_harness=False, cached_eval=cached
    )
    assert selection.use_unified is False
    assert selection.source == "default"


def test_rollout_unified_but_package_unavailable_falls_back_to_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import vibe._experimental_harness as mod

    monkeypatch.setattr(mod, "experimental_harness_available", lambda: False)
    selection = resolve_harness_selection(
        experimental_harness=False,
        legacy_harness=False,
        cached_eval=_cached_response("unified"),
    )
    assert selection.use_unified is False
    assert selection.source == "default"
