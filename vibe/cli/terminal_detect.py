from __future__ import annotations

from enum import Enum
import os
from typing import Literal


class Terminal(Enum):
    VSCODE = "vscode"
    VSCODE_INSIDERS = "vscode_insiders"
    CURSOR = "cursor"
    JETBRAINS = "jetbrains"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    ALACRITTY = "alacritty"
    KITTY = "kitty"
    HYPER = "hyper"
    WINDOWS_TERMINAL = "windows_terminal"
    UNKNOWN = "unknown"


def _is_cursor() -> bool:
    path_indicators = [
        "VSCODE_GIT_ASKPASS_NODE",
        "VSCODE_GIT_ASKPASS_MAIN",
        "VSCODE_IPC_HOOK_CLI",
        "VSCODE_NLS_CONFIG",
    ]
    for var in path_indicators:
        val = os.environ.get(var, "").lower()
        if "cursor" in val:
            return True
    return False


def _detect_vscode_terminal() -> Literal[Terminal.VSCODE, Terminal.VSCODE_INSIDERS]:
    term_version = os.environ.get("TERM_PROGRAM_VERSION", "").lower()
    if term_version.endswith("-insider"):
        return Terminal.VSCODE_INSIDERS

    return Terminal.VSCODE


def _detect_terminal_from_env() -> Terminal | None:
    env_markers: dict[str, Terminal] = {
        "WEZTERM_PANE": Terminal.WEZTERM,
        "GHOSTTY_RESOURCES_DIR": Terminal.GHOSTTY,
        "KITTY_WINDOW_ID": Terminal.KITTY,
        "ALACRITTY_SOCKET": Terminal.ALACRITTY,
        "ALACRITTY_LOG": Terminal.ALACRITTY,
        "WT_SESSION": Terminal.WINDOWS_TERMINAL,
    }
    for var, terminal in env_markers.items():
        if os.environ.get(var):
            return terminal

    if "jetbrains" in os.environ.get("TERMINAL_EMULATOR", "").lower():
        return Terminal.JETBRAINS

    return None


def detect_terminal() -> Terminal:
    term_program = os.environ.get("TERM_PROGRAM", "").lower()

    if term_program == "vscode":
        if _is_cursor():
            return Terminal.CURSOR
        return _detect_vscode_terminal()

    term_map: dict[str, Terminal] = {
        "iterm.app": Terminal.ITERM2,
        "wezterm": Terminal.WEZTERM,
        "ghostty": Terminal.GHOSTTY,
        "alacritty": Terminal.ALACRITTY,
        "kitty": Terminal.KITTY,
        "hyper": Terminal.HYPER,
    }
    if term_program in term_map:
        return term_map[term_program]

    return _detect_terminal_from_env() or Terminal.UNKNOWN
