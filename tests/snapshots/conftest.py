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
def _pin_process_title(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.process_id_label", lambda: "[PID 00000]"
    )


@pytest.fixture(autouse=True)
def _pin_spinner_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop spinners ticking, so a captured frame does not depend on timing.

    Every SpinnerMixin widget advances its frame on a 0.1s interval, so how many
    ticks land before the screenshot varies with machine load. Widgets still
    render their first frame; they just stop moving.
    """
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.spinner.SpinnerMixin.start_spinner_timer",
        lambda self: None,
    )
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.spinner_text.SpinnerText._advance",
        lambda self: None,
    )
