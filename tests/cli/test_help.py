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
    assert "--yolo" in output
    assert "Approves all tool calls without prompting" in output
    assert "selected agent" in output


def test_help_shows_check_upgrade_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        parse_arguments()

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "--check-upgrade" in output
    assert "Check for a Vibe update now" in output


# def test_auto_approve_conflicts_with_agent(
#     monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]


def test_yolo_alias_selects_auto_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", "--yolo"])

    args = parse_arguments()

    assert args.auto_approve is True


@pytest.mark.parametrize("flag", ["--auto-approve", "--yolo"])
def test_auto_approve_aliases_can_be_combined_with_agent(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["vibe", "--agent", "lean", flag])

    args = parse_arguments()

    assert args.agent == "lean"
    assert args.auto_approve is True
