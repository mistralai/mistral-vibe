from __future__ import annotations

import base64
from collections.abc import Callable
import os
import shutil
import subprocess

import pyperclip
from textual.app import App

_PREVIEW_MAX_LENGTH = 40


def _copy_osc52(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    osc52_seq = f"\033]52;c;{encoded}\a"
    if os.environ.get("TMUX"):
        osc52_seq = f"\033Ptmux;\033{osc52_seq}\033\\"

    with open("/dev/tty", "w") as tty:
        tty.write(osc52_seq)
        tty.flush()


def _shorten_preview(texts: list[str]) -> str:
    dense_text = "⏎".join(texts).replace("\n", "⏎")
    if len(dense_text) > _PREVIEW_MAX_LENGTH:
        return f"{dense_text[: _PREVIEW_MAX_LENGTH - 1]}…"
    return dense_text


def _is_remote_session() -> bool:
    # SSH_CONNECTION
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def _has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _is_x11() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "x11" and bool(os.environ.get("DISPLAY"))


def _is_wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" and bool(os.environ.get("WAYLAND_DISPLAY"))


def _copy_x11_clipboard(text: str) -> None:
    # writes to the X11 CLIPBOARD selection (Ctrl+V)
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"),
        check=True,
    )


def _copy_wayland_clipboard(text: str) -> None:
    # Ctrl+V clipboard under Wayland
    subprocess.run(
        ["wl-copy"],
        input=text.encode("utf-8"),
        check=True,
    )


def _clipboard_mode() -> str:
    """Optional override:
    VIBE_CLIPBOARD_MODE=auto|local|remote|osc52
    """
    mode = (os.environ.get("VIBE_CLIPBOARD_MODE") or "auto").strip().lower()
    if mode in {"auto", "local", "remote", "osc52"}:
        return mode
    return "auto"


def _collect_selected_texts(app: App) -> list[str]:
    selected_texts: list[str] = []

    for widget in app.query("*"):
        selection = getattr(widget, "text_selection", None)
        if not selection:
            continue

        try:
            result = widget.get_selection(selection)
        except Exception:
            continue

        if not result:
            continue

        selected_text, _ = result
        if selected_text.strip():
            selected_texts.append(selected_text)

    return selected_texts


def _select_strategies(mode: str, remote: bool) -> list[str]:
    # Strategy order:
    # - remote: OSC52 first (best chance to hit *local* clipboard of user's terminal)
    # - local: prefer real OS clipboard first (wl-copy/xclip), then textual/pyperclip, then osc52.
    if mode == "osc52":
        return ["osc52"]
    if mode == "local":
        return ["os", "textual", "pyperclip", "osc52"]
    if mode == "remote":
        return ["osc52", "os", "textual", "pyperclip"]
    # auto
    return ["osc52", "os", "textual", "pyperclip"] if remote else ["os", "textual", "pyperclip", "osc52"]


def _notify_copied(app: App, preview: str) -> None:
    app.notify(f'"{preview}" copied to clipboard', severity="information", timeout=2)


def _notify_pyperclip_remote(app: App, preview: str) -> None:
    app.notify(
        f'"{preview}" copied (remote clipboard on this machine)',
        severity="warning",
        timeout=3,
    )


def _notify_osc52(app: App) -> None:
    app.notify(
        "Selection sent via OSC52 (terminal must allow clipboard access)",
        severity="warning",
        timeout=3,
    )


def _try_copy_os(text: str) -> bool:
    # Prefer native clipboard tools when available.
    if _is_wayland() and _has_cmd("wl-copy"):
        _copy_wayland_clipboard(text)
        return True
    if _is_x11() and _has_cmd("xclip"):
        _copy_x11_clipboard(text)
        return True
    return False


def _try_copy_textual(app: App, text: str) -> bool:
    app.copy_to_clipboard(text)
    return True


def _try_copy_pyperclip(text: str) -> bool:
    pyperclip.copy(text)
    # Best-effort verification (not always reliable on every backend)
    try:
        if pyperclip.paste() != text:
            raise RuntimeError("pyperclip paste mismatch")
    except Exception:
        pass
    return True


def _try_copy_osc52(text: str) -> bool:
    _copy_osc52(text)
    return True


def copy_selection_to_clipboard(app: App) -> None:
    selected_texts = _collect_selected_texts(app)
    if not selected_texts:
        return

    combined_text = "\n".join(selected_texts)
    preview = _shorten_preview(selected_texts)

    remote = _is_remote_session()
    mode = _clipboard_mode()
    strategies = _select_strategies(mode=mode, remote=remote)

    # Map strategies to callables. Each callable returns True if it performed a copy.
    runners: dict[str, Callable[[], bool]] = {
        "os": lambda: _try_copy_os(combined_text),
        "textual": lambda: _try_copy_textual(app, combined_text),
        "pyperclip": lambda: _try_copy_pyperclip(combined_text),
        "osc52": lambda: _try_copy_osc52(combined_text),
    }

    for strat in strategies:
        runner = runners.get(strat)
        if runner is None:
            continue

        try:
            did_copy = runner()
        except Exception:
            continue

        if not did_copy:
            continue

        if strat == "osc52":
            _notify_osc52(app)
            return

        if strat == "pyperclip" and remote:
            _notify_pyperclip_remote(app, preview)
            return

        _notify_copied(app, preview)
        return

    app.notify(
        "Failed to copy - no clipboard method available",
        severity="warning",
        timeout=3,
    )
