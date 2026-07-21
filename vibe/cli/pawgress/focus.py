from __future__ import annotations

import asyncio
import os
import sys

_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1

_TERM_BUNDLES: dict[str, tuple[str, str]] = {
    "Apple_Terminal": ("com.apple.Terminal", "Terminal"),
    "iTerm.app": ("com.googlecode.iterm2", "iTerm"),
    "ghostty": ("com.mitchellh.ghostty", "Ghostty"),
    "WezTerm": ("com.github.wez.wezterm", "WezTerm"),
    "kitty": ("net.kovidgoyal.kitty", "kitty"),
    "vscode": ("com.microsoft.VSCode", "Visual Studio Code"),
}


def _activate_native(bundle_id: str) -> bool:
    try:
        import AppKit
    except ImportError:
        return False
    workspace_cls = getattr(AppKit, "NSWorkspace", None)
    if workspace_cls is None:
        return False
    try:
        workspace = workspace_cls.sharedWorkspace()
        for app in workspace.runningApplications():
            if app.bundleIdentifier() == bundle_id:
                app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS)
                return True
    except Exception:
        return False
    return False


async def focus_terminal() -> None:
    if sys.platform != "darwin":
        return
    entry = _TERM_BUNDLES.get(os.environ.get("TERM_PROGRAM", ""))
    if entry is None:
        return
    bundle_id, app_name = entry
    if _activate_native(bundle_id):
        return
    proc = await asyncio.create_subprocess_exec(
        "osascript",
        "-e",
        f'tell application "{app_name}" to activate',
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
