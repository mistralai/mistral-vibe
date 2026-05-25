from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import _TYPING_DEBOUNCE_ENV_VAR, _resolve_typing_debounce_s

_TEST_DEFAULT_DEBOUNCE_MS = 1000


@pytest.fixture(autouse=True)
def _restore_default_debounce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app._DEFAULT_TYPING_DEBOUNCE_MS", _TEST_DEFAULT_DEBOUNCE_MS
    )


class TestTypingDebounceEnvVar:
    @pytest.mark.parametrize(
        ("env_value", "expected_s"), [("500", 0.5), ("2000", 2.0), ("0", 0.0)]
    )
    def test_env_var_override(
        self, env_value: str, expected_s: float, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv(_TYPING_DEBOUNCE_ENV_VAR, env_value)
        assert _resolve_typing_debounce_s() == expected_s

    @pytest.mark.parametrize("env_value", [None, "not-a-number", "-100"])
    def test_falls_back_to_default(
        self, env_value: str | None, monkeypatch: pytest.MonkeyPatch
    ):
        if env_value is None:
            monkeypatch.delenv(_TYPING_DEBOUNCE_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(_TYPING_DEBOUNCE_ENV_VAR, env_value)
        assert _resolve_typing_debounce_s() == _TEST_DEFAULT_DEBOUNCE_MS / 1000
