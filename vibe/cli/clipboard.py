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


def _copy_pyperclip(text: str) -> None:
    pyperclip.copy(text)


def _has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _copy_pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)


def _copy_xclip(text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"], input=text.encode("utf-8"), check=True
    )


def _copy_wl_copy(text: str) -> None:
    subprocess.run(["wl-copy"], input=text.encode("utf-8"), check=True)


_CMD_STRATEGIES: list[tuple[str, Callable[[str], None]]] = [
    ("pbcopy", _copy_pbcopy),
    ("xclip", _copy_xclip),
    ("wl-copy", _copy_wl_copy),
]

_COPY_METHODS: list[Callable[[str], None]] = [
    _copy_osc52,
    _copy_pyperclip,
    *[fn for cmd, fn in _CMD_STRATEGIES if _has_cmd(cmd)],
]


def _paste_pyperclip() -> str:
    return pyperclip.paste()


def _paste_pbpaste() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, check=True).stdout.decode(
        "utf-8"
    )


def _paste_xclip() -> str:
    return subprocess.run(
        ["xclip", "-selection", "clipboard", "-o"], capture_output=True, check=True
    ).stdout.decode("utf-8")


def _paste_wl_paste() -> str:
    return subprocess.run(["wl-paste"], capture_output=True, check=True).stdout.decode(
        "utf-8"
    )


_PASTE_CMD_STRATEGIES: list[tuple[str, Callable[[], str]]] = [
    ("pbpaste", _paste_pbpaste),
    ("xclip", _paste_xclip),
    ("wl-paste", _paste_wl_paste),
]

_READ_CLIPBOARD_METHODS: list[Callable[[], str]] = [
    _paste_pyperclip,
    *[fn for cmd, fn in _PASTE_CMD_STRATEGIES if _has_cmd(cmd)],
]


def _read_clipboard() -> str | None:
    for reader in _READ_CLIPBOARD_METHODS:
        try:
            return reader()
        except Exception:
            pass
    return None


def _copy_to_clipboard(text: str) -> None:
    all_strategies_failed = True
    for to_clipboard in _COPY_METHODS:
        try:
            to_clipboard(text)
        except Exception:
            pass
        else:
            all_strategies_failed = False
            if _read_clipboard() == text:
                return

    if all_strategies_failed:
        raise RuntimeError("All clipboard strategies failed")


def _shorten_preview(texts: list[str]) -> str:
    dense_text = "⏎".join(texts).replace("\n", "⏎")
    if len(dense_text) > _PREVIEW_MAX_LENGTH:
        return f"{dense_text[: _PREVIEW_MAX_LENGTH - 1]}…"
    return dense_text


def _get_selected_texts(app: App) -> list[str]:
    selected_texts = []

    for widget in app.query("*"):
        if not hasattr(widget, "text_selection") or not widget.text_selection:
            continue

        selection = widget.text_selection

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


def copy_selection_to_clipboard(app: App, show_toast: bool = True) -> str | None:
    selected_texts = _get_selected_texts(app)
    if not selected_texts:
        return None

    combined_text = "\n".join(selected_texts)
    try:
        _copy_to_clipboard(combined_text)
        if show_toast:
            app.notify(
                f'"{_shorten_preview(selected_texts)}" copied to clipboard',
                severity="information",
                timeout=2,
                markup=False,
            )
        return combined_text
    except Exception:
        app.notify(
            "Failed to copy - clipboard not available", severity="warning", timeout=3
        )
        return None
