from __future__ import annotations

import base64
from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pyperclip
from textual.app import App

from vibe.core.types import ImageAttachment


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
    subprocess.run(
        ["pbcopy"], input=text.encode("utf-8"), check=True, stderr=subprocess.DEVNULL
    )


def _copy_xclip(text: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"),
        check=True,
        stderr=subprocess.DEVNULL,
    )


def _copy_wl_copy(text: str) -> None:
    subprocess.run(
        ["wl-copy"], input=text.encode("utf-8"), check=True, stderr=subprocess.DEVNULL
    )


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


def _get_selected_texts(app: App) -> list[str]:
    selected_texts = []

    for widget in app.query("*"):
        try:
            if not hasattr(widget, "text_selection") or not widget.text_selection:
                continue
            selection = widget.text_selection
            result = widget.get_selection(selection)
        except Exception:
            continue

        if not result:
            continue

        selected_text, _ = result
        if selected_text.strip():
            selected_texts.append(selected_text)

    return selected_texts


def copy_text_to_clipboard(
    app: App,
    text: str,
    *,
    show_toast: bool = True,
    success_message: str = "Copied to clipboard",
) -> str | None:
    if not text:
        return None

    if try_copy_text_to_clipboard(text):
        if show_toast:
            app.notify(success_message, severity="information", timeout=2, markup=False)
        return text

    app.notify(
        "Failed to copy - clipboard not available", severity="warning", timeout=3
    )
    return None


def try_copy_text_to_clipboard(text: str) -> bool:
    if not text:
        return False

    try:
        _copy_to_clipboard(text)
    except Exception:
        return False

    return True


def copy_selection_to_clipboard(app: App, show_toast: bool = True) -> str | None:
    selected_texts = _get_selected_texts(app)
    if not selected_texts:
        return None

    return copy_text_to_clipboard(
        app,
        "\n".join(selected_texts),
        show_toast=show_toast,
        success_message="Selection copied to clipboard",
    )


# ── Clipboard image support ──────────────────────────────────────────────────


def has_clipboard_image() -> bool:
    """Return True if the system clipboard currently holds an image."""
    if sys.platform == "darwin":
        return _has_clipboard_image_macos()
    return _has_clipboard_image_linux()


def get_clipboard_image_attachment() -> ImageAttachment | None:
    """Extract a clipboard image and return an ImageAttachment, or None."""
    if sys.platform == "darwin":
        return _get_clipboard_image_attachment_macos()
    return _get_clipboard_image_attachment_linux()


def _has_clipboard_image_macos() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", "clipboard info"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        info = result.stdout.lower()
        return any(tok in info for tok in ("png", "jpeg", "jpg", "tiff", "gif"))
    except Exception:
        return False


_MACOS_IMAGE_FORMATS: tuple[tuple[str, str], ...] = (
    ("«class PNGf»", "image/png"),
    ("«class TIFF»", "image/tiff"),
    ("JPEG picture", "image/jpeg"),
    ("GIF picture", "image/gif"),
    ("«class BMP »", "image/bmp"),
)


def _get_clipboard_image_attachment_macos() -> ImageAttachment | None:
    for applescript_type, mime in _MACOS_IMAGE_FORMATS:
        att = _extract_macos_clipboard_type(applescript_type, mime)
        if att:
            return att
    return None


def _extract_macos_clipboard_type(
    applescript_type: str, mime: str
) -> ImageAttachment | None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="vibe_clipboard_"))
    tmp_bin = tmp_dir / "clipboard.bin"
    png_path = tmp_dir / "clipboard.png"
    try:
        script = f"""
set tmpFile to (POSIX file "{tmp_bin}")
try
    set imgData to the clipboard as {applescript_type}
    set fileRef to (open for access tmpFile with write permission)
    write imgData to fileRef
    close access fileRef
    return "ok"
on error
    try
        close access tmpFile
    end try
    return "error"
end try
"""
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() != "ok":
            return None

        final_path = tmp_bin
        final_mime = mime
        if mime != "image/png" and shutil.which("sips"):
            conv = subprocess.run(
                ["sips", "-s", "format", "png", str(tmp_bin), "--out", str(png_path)],
                capture_output=True,
                timeout=5,
            )
            if conv.returncode == 0:
                final_path = png_path
                final_mime = "image/png"

        if not final_path.exists() or final_path.stat().st_size == 0:
            return None
        return ImageAttachment(
            path=final_path, alias="clipboard image", mime_type=final_mime
        )
    except Exception:
        return None


def _has_clipboard_image_linux() -> bool:
    for cmd in (
        ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
        ["wl-paste", "--list-types"],
    ):
        if shutil.which(cmd[0]):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
                return any(
                    t in r.stdout for t in ("image/png", "image/jpeg", "image/gif")
                )
            except Exception:
                pass
    return False


def _get_clipboard_image_attachment_linux() -> ImageAttachment | None:
    for cmd, mime in (
        (["xclip", "-selection", "clipboard", "-t", "image/png", "-o"], "image/png"),
        (["wl-paste", "--type", "image/png"], "image/png"),
    ):
        if shutil.which(cmd[0]):
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=5)
                if r.returncode == 0 and r.stdout:
                    tmp_dir = Path(tempfile.mkdtemp(prefix="vibe_clipboard_"))
                    tmp_path = tmp_dir / "clipboard.png"
                    tmp_path.write_bytes(r.stdout)
                    return ImageAttachment(
                        path=tmp_path, alias="clipboard image", mime_type=mime
                    )
            except Exception:
                pass
    return None
