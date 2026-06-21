from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from vibe.cli.terminal_detect import Terminal, detect_terminal


def _detect_with(env: dict[str, str]) -> Terminal:
    with patch.dict(os.environ, env, clear=True):
        return detect_terminal()


def test_detects_vscode() -> None:
    assert _detect_with({"TERM_PROGRAM": "vscode"}) is Terminal.VSCODE


def test_detects_vscode_insiders() -> None:
    assert (
        _detect_with({"TERM_PROGRAM": "vscode", "TERM_PROGRAM_VERSION": "1.2-insider"})
        is Terminal.VSCODE_INSIDERS
    )


def test_detects_cursor_from_vscode_environment() -> None:
    assert (
        _detect_with({
            "TERM_PROGRAM": "vscode",
            "VSCODE_IPC_HOOK_CLI": "/Applications/Cursor.app/hook",
        })
        is Terminal.CURSOR
    )


@pytest.mark.parametrize(
    ("term_program", "terminal"),
    [
        ("iterm.app", Terminal.ITERM2),
        ("wezterm", Terminal.WEZTERM),
        ("ghostty", Terminal.GHOSTTY),
        ("alacritty", Terminal.ALACRITTY),
        ("kitty", Terminal.KITTY),
        ("hyper", Terminal.HYPER),
    ],
)
def test_detects_term_program_mapping(term_program: str, terminal: Terminal) -> None:
    assert _detect_with({"TERM_PROGRAM": term_program}) is terminal


@pytest.mark.parametrize(
    ("env_var", "terminal"),
    [
        ("WEZTERM_PANE", Terminal.WEZTERM),
        ("GHOSTTY_RESOURCES_DIR", Terminal.GHOSTTY),
        ("KITTY_WINDOW_ID", Terminal.KITTY),
        ("ALACRITTY_SOCKET", Terminal.ALACRITTY),
        ("ALACRITTY_LOG", Terminal.ALACRITTY),
        ("WT_SESSION", Terminal.WINDOWS_TERMINAL),
    ],
)
def test_detects_environment_marker_fallback(env_var: str, terminal: Terminal) -> None:
    assert _detect_with({env_var: "1"}) is terminal


def test_detects_jetbrains_environment_fallback() -> None:
    assert (
        _detect_with({"TERMINAL_EMULATOR": "JetBrains-JediTerm"}) is Terminal.JETBRAINS
    )


def test_returns_unknown_without_markers() -> None:
    assert _detect_with({}) is Terminal.UNKNOWN
