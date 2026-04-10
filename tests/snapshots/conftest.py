from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _pin_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()


@pytest.fixture(autouse=True)
def _pin_banner_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.banner.banner.__version__", "0.0.0"
    )


@pytest.fixture(autouse=True)
def _pin_alt_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vibe.cli.textual_ui.widgets.rewind_app.ALT_KEY", "Alt")
