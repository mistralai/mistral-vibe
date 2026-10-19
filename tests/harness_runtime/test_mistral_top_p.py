from __future__ import annotations

from mistralai_vibe_local_harness.vibe.adapters.mistral import _mistral_top_p


def test_greedy_sampling_sets_top_p_when_unset() -> None:
    assert _mistral_top_p(0.0, None) == 1.0


def test_explicit_top_p_is_preserved() -> None:
    assert _mistral_top_p(0.0, 0.9) == 0.9
    assert _mistral_top_p(1.0, 0.8) == 0.8


def test_nonzero_temperature_omits_top_p_by_default() -> None:
    assert _mistral_top_p(0.7, None) is None
