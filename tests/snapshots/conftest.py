from __future__ import annotations

import time

import pytest


@pytest.fixture(autouse=True)
def _pin_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()


@pytest.fixture(autouse=True)
def _pin_snapshot_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture(autouse=True)
def _pin_banner_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.banner.banner.__version__", "0.0.0"
    )


@pytest.fixture(autouse=True)
def _freeze_animations(monkeypatch: pytest.MonkeyPatch) -> None:
    # Snapshots must not capture whichever animation frame the timer happened to be
    # on. TEXTUAL_ANIMATIONS is read into a module constant at import time, so the
    # env var is already too late by now; App.__init__ reads this attribute.
    monkeypatch.setattr("textual.constants.TEXTUAL_ANIMATIONS", "none")
