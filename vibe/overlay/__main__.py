from __future__ import annotations

import argparse
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from vibe.core.pawgress.protocol import ControlMsg
from vibe.overlay.hotkey import HotkeyBridge, install_toggle_hotkey
from vibe.overlay.macos import hide_dock_icon, make_visible_on_all_spaces
from vibe.overlay.reader import StdinReader
from vibe.overlay.server import OverlayServer
from vibe.overlay.singleton import acquire_overlay_lock, socket_path
from vibe.overlay.window import IslandWindow

_GRACE_MS = 2000


def main() -> None:  # noqa: PLR0915
    parser = argparse.ArgumentParser(prog="vibe.overlay")
    parser.add_argument(
        "--stdin", action="store_true", help="demo mode: read a stub feed from stdin"
    )
    args = parser.parse_args()

    app = QApplication(sys.argv)
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    window = IslandWindow()
    window.show()
    make_visible_on_all_spaces(window)
    hide_dock_icon()
    QTimer.singleShot(200, hide_dock_icon)

    bridge = HotkeyBridge()
    bridge.triggered.connect(window.toggle_visibility)
    install_toggle_hotkey(bridge)

    if args.stdin:
        reader = StdinReader()
        reader.received.connect(window.update_state)
        reader.start()
        sys.exit(app.exec())

    lock_fd = acquire_overlay_lock()
    if lock_fd is None:
        # Another overlay already owns the socket — nothing to do.
        return

    server = OverlayServer()
    if not server.listen(str(socket_path())):
        os.close(lock_fd)
        return

    def refresh() -> None:
        reg = server.registry
        window.set_sessions(reg.entries(), reg.active_sid, reg.active_index())

    def on_empty() -> None:
        def maybe_quit() -> None:
            if server.registry.count() == 0:
                app.quit()

        QTimer.singleShot(_GRACE_MS, maybe_quit)

    def on_control(message: ControlMsg) -> None:
        sid = window.active_control_sid
        if sid is not None:
            server.send_control(sid, message)

    def on_row_selected(sid: str) -> None:
        server.registry.set_active(sid)
        window.enter_detail()
        refresh()

    def on_open_directory() -> None:
        window.enter_directory()
        refresh()

    def on_attention(sid: str) -> None:
        # A hidden overlay resurfaces on a notify-worthy update, landing on the
        # directory (not a specific detail) so concurrent updates don't fight.
        if window.isVisible():
            return
        server.registry.set_active(sid)
        window.enter_directory()
        window.show()
        window.raise_()
        make_visible_on_all_spaces(window)
        refresh()

    # Keyboard navigation (global hotkeys), interpreted by current view mode.
    def key_left() -> None:  # cmd+alt+left: detail → directory
        if window.mode == "detail":
            window.enter_directory()
            refresh()

    def key_up() -> None:  # cmd+alt+up: directory → move selection up
        if window.mode == "directory":
            server.registry.activate_prev()
            refresh()

    def key_down() -> None:  # cmd+alt+down: directory → move selection down
        if window.mode == "directory":
            server.registry.activate_next()
            refresh()

    def key_enter() -> None:  # cmd+shift+right: directory → open selected detail
        if window.mode == "directory":
            window.enter_detail()
            refresh()

    server.state_changed.connect(refresh)
    server.empty.connect(on_empty)
    server.attention.connect(on_attention)
    window.control_requested.connect(on_control)
    window.row_selected.connect(on_row_selected)
    window.open_directory_requested.connect(on_open_directory)
    bridge.nav_left.connect(key_left)
    bridge.nav_up.connect(key_up)
    bridge.nav_down.connect(key_down)
    bridge.nav_enter.connect(key_enter)
    refresh()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
