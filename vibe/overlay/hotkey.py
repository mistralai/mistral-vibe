from __future__ import annotations

from PySide6.QtCore import QObject, Signal

_keep_alive: list[object] = []


class HotkeyBridge(QObject):
    triggered = Signal()  # alt+enter        → toggle visibility (unchanged)
    nav_left = Signal()  # cmd+shift+left   → detail: back to directory
    nav_up = Signal()  # cmd+shift+up     → directory: move selection up
    nav_down = Signal()  # cmd+shift+down   → directory: move selection down
    nav_enter = Signal()  # cmd+shift+right  → directory: open selected session


def install_toggle_hotkey(bridge: HotkeyBridge) -> None:
    try:
        from pynput import keyboard
    except ImportError:
        return
    try:
        listener = keyboard.GlobalHotKeys({
            "<alt>+<enter>": bridge.triggered.emit,
            "<cmd>+<shift>+<left>": bridge.nav_left.emit,
            "<cmd>+<shift>+<up>": bridge.nav_up.emit,
            "<cmd>+<shift>+<down>": bridge.nav_down.emit,
            "<cmd>+<shift>+<right>": bridge.nav_enter.emit,
        })
        listener.start()
        _keep_alive.append(listener)
    except Exception:
        return
