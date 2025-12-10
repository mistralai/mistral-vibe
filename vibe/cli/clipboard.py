from __future__ import annotations

import pyperclip
from textual.app import App


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

    try:
        pyperclip.copy(combined_text)
        app.notify("Selection added to clipboard", severity="information", timeout=2)
    except Exception:
        app.notify(
            "Use Ctrl+c to copy selections in Vibe", severity="warning", timeout=3
        )
