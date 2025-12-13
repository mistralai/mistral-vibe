from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any


class Terminal(Enum):
    VSCODE = "vscode"
    CURSOR = "cursor"
    ITERM2 = "iterm2"
    WEZTERM = "wezterm"
    GHOSTTY = "ghostty"
    UNKNOWN = "unknown"


@dataclass
class SetupResult:
    success: bool
    terminal: Terminal
    message: str
    requires_restart: bool = False


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


def detect_terminal() -> Terminal:
    term_program = os.environ.get("TERM_PROGRAM", "").lower()

    if term_program == "vscode":
        if _is_cursor():
            return Terminal.CURSOR
        return Terminal.VSCODE

    term_map = {
        "iterm.app": Terminal.ITERM2,
        "wezterm": Terminal.WEZTERM,
        "ghostty": Terminal.GHOSTTY,
    }
    if term_program in term_map:
        return term_map[term_program]

    if os.environ.get("WEZTERM_PANE"):
        return Terminal.WEZTERM
    if os.environ.get("GHOSTTY_RESOURCES_DIR"):
        return Terminal.GHOSTTY

    return Terminal.UNKNOWN


def _get_vscode_keybindings_path() -> Path | None:
    system = platform.system()

    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Code" / "User"
    elif system == "Linux":
        base = Path.home() / ".config" / "Code" / "User"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            base = Path(appdata) / "Code" / "User"
        else:
            return None
    else:
        return None

    return base / "keybindings.json"


def _get_cursor_keybindings_path() -> Path | None:
    system = platform.system()

    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Cursor" / "User"
    elif system == "Linux":
        base = Path.home() / ".config" / "Cursor" / "User"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            base = Path(appdata) / "Cursor" / "User"
        else:
            return None
    else:
        return None

    return base / "keybindings.json"


def _parse_keybindings(content: str) -> list[dict[str, Any]]:
    content = content.strip()
    if not content or content.startswith("//"):
        return []

    lines = [line for line in content.split("\n") if not line.strip().startswith("//")]
    clean_content = "\n".join(lines)

    try:
        return json.loads(clean_content)
    except json.JSONDecodeError:
        return []


def _setup_vscode_like_terminal(terminal: Terminal) -> SetupResult:
    """Setup keybindings for VSCode or Cursor."""
    if terminal == Terminal.CURSOR:
        keybindings_path = _get_cursor_keybindings_path()
        editor_name = "Cursor"
    else:
        keybindings_path = _get_vscode_keybindings_path()
        editor_name = "VSCode"

    if keybindings_path is None:
        return SetupResult(
            success=False,
            terminal=terminal,
            message=f"Could not determine keybindings path for {editor_name}",
        )

    new_binding = {
        "key": "shift+enter",
        "command": "workbench.action.terminal.sendSequence",
        "args": {"text": "\u001b[13;2u"},
        "when": "terminalFocus",
    }

    try:
        keybindings = _read_existing_keybindings(keybindings_path)

        if _has_shift_enter_binding(keybindings):
            return SetupResult(
                success=True,
                terminal=terminal,
                message=f"Shift+Enter already configured in {editor_name}",
            )

        keybindings.append(new_binding)
        keybindings_path.write_text(json.dumps(keybindings, indent=2) + "\n")

        return SetupResult(
            success=True,
            terminal=terminal,
            message=f"Added Shift+Enter binding to {keybindings_path}",
            requires_restart=True,
        )

    except Exception as e:
        return SetupResult(
            success=False,
            terminal=terminal,
            message=f"Failed to configure {editor_name}: {e}",
        )


def _read_existing_keybindings(keybindings_path: Path) -> list[dict[str, Any]]:
    if keybindings_path.exists():
        content = keybindings_path.read_text()
        return _parse_keybindings(content)
    keybindings_path.parent.mkdir(parents=True, exist_ok=True)
    return []


def _has_shift_enter_binding(keybindings: list[dict[str, Any]]) -> bool:
    for binding in keybindings:
        if (
            binding.get("key") == "shift+enter"
            and binding.get("command") == "workbench.action.terminal.sendSequence"
            and binding.get("when") == "terminalFocus"
        ):
            return True
    return False


def _setup_iterm2() -> SetupResult:
    if platform.system() != "Darwin":
        return SetupResult(
            success=False,
            terminal=Terminal.ITERM2,
            message="iTerm2 is only available on macOS",
        )

    plist_key = "0xd-0x20000-0x24"
    plist_value = """<dict>
    <key>Text</key>
    <string>\\n</string>
    <key>Action</key>
    <integer>12</integer>
    <key>Version</key>
    <integer>1</integer>
    <key>Keycode</key>
    <integer>13</integer>
    <key>Modifiers</key>
    <integer>131072</integer>
</dict>"""

    try:
        result = subprocess.run(
            ["defaults", "read", "com.googlecode.iterm2", "GlobalKeyMap"],
            capture_output=True,
            text=True,
        )

        if plist_key in result.stdout:
            return SetupResult(
                success=True,
                terminal=Terminal.ITERM2,
                message="Shift+Enter already configured in iTerm2",
            )

        subprocess.run(
            [
                "defaults",
                "write",
                "com.googlecode.iterm2",
                "GlobalKeyMap",
                "-dict-add",
                plist_key,
                plist_value,
            ],
            check=True,
            capture_output=True,
        )

        return SetupResult(
            success=True,
            terminal=Terminal.ITERM2,
            message="Added Shift+Enter binding to iTerm2 preferences",
            requires_restart=True,
        )

    except subprocess.CalledProcessError as e:
        return SetupResult(
            success=False,
            terminal=Terminal.ITERM2,
            message=f"Failed to configure iTerm2: {e.stderr}",
        )
    except Exception as e:
        return SetupResult(
            success=False,
            terminal=Terminal.ITERM2,
            message=f"Failed to configure iTerm2: {e}",
        )


def _setup_wezterm() -> SetupResult:
    wezterm_config = Path.home() / ".wezterm.lua"

    key_binding = """{
    key = "Enter",
    mods = "SHIFT",
    action = wezterm.action.SendString("\\x1b[13;2u"),
  }"""

    try:
        if wezterm_config.exists():
            content = wezterm_config.read_text()

            if 'mods = "SHIFT"' in content and 'key = "Enter"' in content:
                return SetupResult(
                    success=True,
                    terminal=Terminal.WEZTERM,
                    message="Shift+Enter already configured in WezTerm",
                )

            if "keys = {" in content:
                content = content.replace("keys = {", f"keys = {{\n  {key_binding},")
            else:
                return SetupResult(
                    success=False,
                    terminal=Terminal.WEZTERM,
                    message="Please manually add the following to your .wezterm.lua:\n\n"
                    f"  keys = {{\n    {key_binding}\n  }}",
                )
        else:
            content = f"""local wezterm = require 'wezterm'

return {{
  keys = {{
    {key_binding}
  }},
}}
"""

        wezterm_config.write_text(content)

        return SetupResult(
            success=True,
            terminal=Terminal.WEZTERM,
            message=f"Added Shift+Enter binding to {wezterm_config}",
            requires_restart=True,
        )

    except Exception as e:
        return SetupResult(
            success=False,
            terminal=Terminal.WEZTERM,
            message=f"Failed to configure WezTerm: {e}",
        )


def _setup_ghostty() -> SetupResult:
    system = platform.system()

    if system == "Darwin":
        config_path = (
            Path.home()
            / "Library"
            / "Application Support"
            / "com.mitchellh.ghostty"
            / "config"
        )
    elif system == "Linux":
        xdg_config = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        config_path = Path(xdg_config) / "ghostty" / "config"
    else:
        return SetupResult(
            success=False,
            terminal=Terminal.GHOSTTY,
            message="Ghostty configuration path unknown for this OS",
        )

    keybind_line = "keybind = shift+enter=text:\\x1b[13;2u"

    try:
        if config_path.exists():
            content = config_path.read_text()

            if "shift+enter" in content.lower():
                return SetupResult(
                    success=True,
                    terminal=Terminal.GHOSTTY,
                    message="Shift+Enter already configured in Ghostty",
                )

            if not content.endswith("\n"):
                content += "\n"
            content += keybind_line + "\n"
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            content = keybind_line + "\n"

        config_path.write_text(content)

        return SetupResult(
            success=True,
            terminal=Terminal.GHOSTTY,
            message=f"Added Shift+Enter binding to {config_path}",
            requires_restart=True,
        )

    except Exception as e:
        return SetupResult(
            success=False,
            terminal=Terminal.GHOSTTY,
            message=f"Failed to configure Ghostty: {e}",
        )


def setup_terminal() -> SetupResult:
    terminal = detect_terminal()

    match terminal:
        case Terminal.VSCODE:
            return _setup_vscode_like_terminal(Terminal.VSCODE)
        case Terminal.CURSOR:
            return _setup_vscode_like_terminal(Terminal.CURSOR)
        case Terminal.ITERM2:
            return _setup_iterm2()
        case Terminal.WEZTERM:
            return _setup_wezterm()
        case Terminal.GHOSTTY:
            return _setup_ghostty()
        case Terminal.UNKNOWN:
            return SetupResult(
                success=False,
                terminal=Terminal.UNKNOWN,
                message="Could not detect terminal. Supported terminals:\n"
                "- VSCode\n"
                "- Cursor\n"
                "- iTerm2\n"
                "- WezTerm\n"
                "- Ghostty\n\n"
                "You can manually configure Shift+Enter to send: \\x1b[13;2u",
            )
