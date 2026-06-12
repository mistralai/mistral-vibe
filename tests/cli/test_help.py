from __future__ import annotations

import pytest

from vibe.cli.entrypoint import parse_arguments


def test_help_shows_auto_approve_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--auto-approve" in output
    assert "Shortcut for --agent auto-approve" in output


def test_auto_approve_conflicts_with_agent(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", "--agent", "plan", "--auto-approve"])

    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()

    assert exc_info.value.code == 2
    assert "not allowed with argument --agent" in capsys.readouterr().err
