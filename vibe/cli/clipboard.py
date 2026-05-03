from __future__ import annotations

import base64
from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Literal

import pyperclip
from textual.app import App

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_REQUEST = 8

ImageMediaType = Literal["image/png", "image/jpeg", "image/webp", "image/gif"]

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8\xff"
_GIF_SIGS = (b"GIF87a", b"GIF89a")
_WEBP_HEADER_LEN = 12

_OSASCRIPT_SAVE_IMAGE = """\
on run argv
    set destPath to item 1 of argv
    set imageData to missing value
    try
        set imageData to (the clipboard as «class PNGf»)
    end try
    if imageData is missing value then
        try
            set imageData to (the clipboard as JPEG picture)
        end try
    end if
    if imageData is missing value then
        return "no_image"
    end if
    try
        set fp to open for access (POSIX file destPath) with write permission
        set eof of fp to 0
        write imageData to fp
        close access fp
    on error errMsg
        try
            close access (POSIX file destPath)
        end try
        return "write_failed: " & errMsg
    end try
    return "ok"
end run
"""


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


def _paste_image_osascript() -> bytes | None:
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp = Path(name)
    try:
        result = subprocess.run(
            ["osascript", "-e", _OSASCRIPT_SAVE_IMAGE, str(tmp)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        if result.stdout.strip() != "ok":
            return None
        if not tmp.exists() or tmp.stat().st_size == 0:
            return None
        return tmp.read_bytes()
    finally:
        tmp.unlink(missing_ok=True)


def _paste_image_xclip() -> bytes | None:
    result = subprocess.run(
        ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _paste_image_wl_paste() -> bytes | None:
    result = subprocess.run(
        ["wl-paste", "-t", "image/png"],
        capture_output=True,
        check=False,
        timeout=5,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


_IMAGE_CMD_STRATEGIES: list[tuple[str, Callable[[], bytes | None]]] = [
    ("osascript", _paste_image_osascript),
    ("xclip", _paste_image_xclip),
    ("wl-paste", _paste_image_wl_paste),
]


def _detect_image_format(data: bytes) -> ImageMediaType | None:
    """Identify a Mistral-supported image format from its magic bytes.

    Returns None for anything outside PNG / JPEG / WEBP / GIF, including TIFF,
    which the macOS clipboard sometimes serves but the Mistral API rejects.
    """
    if data.startswith(_PNG_SIG):
        return "image/png"
    if data.startswith(_JPEG_SIG):
        return "image/jpeg"
    if data.startswith(_GIF_SIGS):
        return "image/gif"
    if (
        len(data) >= _WEBP_HEADER_LEN
        and data[:4] == b"RIFF"
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def _read_clipboard_image() -> tuple[bytes, ImageMediaType] | None:
    """Read an image from the system clipboard as raw bytes.

    Returns None when the clipboard holds no image, no supported platform
    tool is installed, the image exceeds MAX_IMAGE_BYTES, or the bytes are
    not in a Mistral-supported format (PNG / JPEG / WEBP / GIF). Format is
    detected from magic bytes rather than trusted from the source helper,
    since osascript may fall back to JPEG when PNG isn't on the clipboard.
    """
    for cmd, reader in _IMAGE_CMD_STRATEGIES:
        if not _has_cmd(cmd):
            continue
        try:
            data = reader()
        except Exception:
            continue
        if not data:
            continue
        if len(data) > MAX_IMAGE_BYTES:
            return None
        media_type = _detect_image_format(data)
        if media_type is None:
            return None
        return data, media_type
    return None


def _encode_image_data_url(data: bytes, media_type: ImageMediaType) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


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

    try:
        _copy_to_clipboard(text)
        if show_toast:
            app.notify(success_message, severity="information", timeout=2, markup=False)
        return text
    except Exception:
        app.notify(
            "Failed to copy - clipboard not available", severity="warning", timeout=3
        )
        return None


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
