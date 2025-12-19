from __future__ import annotations

import base64
import os
import sys

import pyperclip
from textual.app import App

from vibe.core.utils import logger

_PREVIEW_MAX_LENGTH = 40


def _is_windows() -> bool:
    return sys.platform == "win32"


def _copy_osc52(text: str) -> None:
    # OSC52 requires /dev/tty which doesn't exist on Windows
    if _is_windows():
        raise OSError("OSC52 not supported on Windows")

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


def copy_selection_to_clipboard(app: App) -> None:
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

    if not selected_texts:
        return

    combined_text = "\n".join(selected_texts)

    # On Windows, prefer pyperclip first as it uses native Windows clipboard API
    # OSC52 requires /dev/tty which doesn't exist on Windows
    if _is_windows():
        copy_methods = [pyperclip.copy, app.copy_to_clipboard]
    else:
        copy_methods = [_copy_osc52, pyperclip.copy, app.copy_to_clipboard]

    for copy_fn in copy_methods:
        try:
            copy_fn(combined_text)
        except Exception as e:
            fn_name = getattr(copy_fn, "__name__", str(copy_fn))
            logger.debug("Clipboard method %s failed: %s", fn_name, e)
            continue
        else:
            app.notify(
                f'"{_shorten_preview(selected_texts)}" copied to clipboard',
                severity="information",
                timeout=2,
            )
            break
    else:
        app.notify(
            "Failed to copy - no clipboard method available",
            severity="warning",
            timeout=3,
        )
