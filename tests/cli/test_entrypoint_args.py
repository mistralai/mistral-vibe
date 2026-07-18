from __future__ import annotations

import pytest

from vibe.cli.entrypoint import parse_arguments


def _parse(monkeypatch: pytest.MonkeyPatch, argv: list[str]):
    monkeypatch.setattr("sys.argv", ["vibe", *argv])
    return parse_arguments()


def test_disabled_tools_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse(monkeypatch, [])
    assert args.disabled_tools is None


def test_disabled_tools_appends_multiple(monkeypatch: pytest.MonkeyPatch) -> None:
    args = _parse(monkeypatch, ["--disabled-tools", "bash", "--disabled-tools", "web*"])
    assert args.disabled_tools == ["bash", "web*"]


def test_enabled_and_disabled_tools_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--enabled-tools", "read", "--disabled-tools", "bash"])
    assert args.enabled_tools == ["read"]
    assert args.disabled_tools == ["bash"]


def test_watchdog_flags_parse_without_mutating_other_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse(monkeypatch, ["--watchdog", "--watchdog-replay", "run-1"])

    assert args.watchdog is True
    assert args.watchdog_replay == "run-1"
