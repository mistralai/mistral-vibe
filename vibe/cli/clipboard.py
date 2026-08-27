from __future__ import annotations

import base64
from dataclasses import dataclass
import os
from typing import NamedTuple

from textual.app import App
from textual.widget import Widget
from textual.widgets import Input, TextArea

NATIVE_COPY_HINT = (
    "if paste fails, hold Shift (Option in iTerm2, Fn in Terminal.app) "
    "while selecting for native copy"
)


@dataclass(frozen=True)
class ClipboardCopyResult:
    text: str
    verified: bool


def _copy_osc52(text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    osc52_seq = f"\033]52;c;{encoded}\a"
    if os.environ.get("TMUX"):
        osc52_seq = f"\033Ptmux;\033{osc52_seq}\033\\"

    with open("/dev/tty", "w") as tty:
        tty.write(osc52_seq)
        tty.flush()


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n")


def _copy_native(text: str) -> bool:
    import pyperclip

    pyperclip.copy(text)
    try:
        return _normalize_newlines(pyperclip.paste()) == _normalize_newlines(text)
    except Exception:
        return False


def _is_ssh_session() -> bool:
    return bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY"))


def copy_to_clipboard(text: str) -> bool:
    if not text:
        return False

    verified = False
    if not _is_ssh_session():
        try:
            verified = _copy_native(text)
        except Exception:
            verified = False

    try:
        _copy_osc52(text)
    except Exception:
        pass

    return verified


class _SelectedText(NamedTuple):
    column: int  # widget's rendered left column, used to restore indentation
    text: str  # selected text inside the widget
    ending: str  # the widget's own line ending ("\n", " ", "" ...)


def _widget_column(widget: Widget) -> int:
    """Return the widget's rendered left column"""
    try:
        return widget.region.x
    except Exception:
        return 0


def _get_selected_texts(app: App) -> list[_SelectedText]:
    """Collect the selected text of every widget."""
    selected_texts: list[_SelectedText] = []

    for widget in app.query("*"):
        if isinstance(widget, (TextArea, Input)):
            if (selected_text := widget.selected_text).strip():
                selected_texts.append(
                    _SelectedText(_widget_column(widget), selected_text, "\n")
                )
            continue

        try:
            if not hasattr(widget, "text_selection") or not widget.text_selection:
                continue
            selection = widget.text_selection
            result = widget.get_selection(selection)
        except Exception:
            continue

        if not result:
            continue

        selected_text, ending = result
        if selected_text.strip():
            selected_texts.append(
                _SelectedText(
                    _widget_column(widget),
                    selected_text,
                    ending if isinstance(ending, str) else "\n",
                )
            )

    return selected_texts


def _join_selected_texts(selected_texts: list[_SelectedText]) -> str:
    """Join selections, honoring each widget's line ending and restoring per-line indentation."""
    base_column = min(item.column for item in selected_texts)

    parts: list[str] = []
    at_line_start = True
    for item in selected_texts:
        if at_line_start:
            parts.append(" " * max(0, item.column - base_column))
        parts.append(item.text)
        parts.append(item.ending)
        at_line_start = item.ending.endswith("\n")

    return "".join(parts).rstrip("\n")


def copy_text_to_clipboard(
    app: App,
    text: str,
    *,
    show_toast: bool = True,
    success_message: str = "Copied to clipboard",
) -> ClipboardCopyResult | None:
    if not text:
        return None

    verified = copy_to_clipboard(text)

    if show_toast:
        if verified:
            app.notify(success_message, severity="information", timeout=2, markup=False)
        else:
            app.notify(
                f"{success_message} · {NATIVE_COPY_HINT}",
                severity="information",
                timeout=6,
                markup=False,
            )
    return ClipboardCopyResult(text=text, verified=verified)


def copy_selection_to_clipboard(
    app: App, show_toast: bool = True
) -> ClipboardCopyResult | None:
    selected_texts = _get_selected_texts(app)
    if not selected_texts:
        return None

    joined = _join_selected_texts(selected_texts)

    return copy_text_to_clipboard(
        app,
        joined,
        show_toast=show_toast,
        success_message="Selection copied to clipboard",
    )
