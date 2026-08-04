from __future__ import annotations

from enum import StrEnum


class TerminalEmulator(StrEnum):
    VSCODE = "vscode"
    VSCODE_INSIDERS = "vscode_insiders"
    CURSOR = "cursor"
    JETBRAINS = "jetbrains"
    APPLE_TERMINAL = "apple_terminal"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    ALACRITTY = "alacritty"
    KITTY = "kitty"
    HYPER = "hyper"
    WINDOWS_TERMINAL = "windows_terminal"
    UNKNOWN = "unknown"


__all__ = ["TerminalEmulator"]
