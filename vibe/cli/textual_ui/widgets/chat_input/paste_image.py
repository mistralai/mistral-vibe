from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
from typing import TYPE_CHECKING

from humanize import naturalsize

from vibe.cli.constants import CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.core.logger import logger
from vibe.core.types import MAX_IMAGE_BYTES

if TYPE_CHECKING:
    from vibe.cli.textual_ui.app import VibeApp

_READ_TIMEOUT_S = 5.0
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_MAX_SAME_SECOND_COLLISIONS = 1000


def is_clipboard_image_paste_supported() -> bool:
    return platform.system() == CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM


def read_clipboard_image() -> bytes | None:
    if not is_clipboard_image_paste_supported():
        return None
    for reader in _readers_for_platform():
        try:
            data = reader()
        except Exception:
            continue
        if data and data.startswith(_PNG_MAGIC):
            return data
    return None


def _readers_for_platform() -> list[Callable[[], bytes | None]]:
    if platform.system() == CLIPBOARD_IMAGE_PASTE_SUPPORTED_SYSTEM:
        return [_read_macos]
    return []


def _read_macos() -> bytes | None:
    # Preview's Copy, Screenshot, browser Copy Image, etc. each pick their own
    # image flavor on the pasteboard. Try PNG first (most common), then TIFF
    # converted via `sips` (Preview frequently only writes TIFF picture).
    data = _read_macos_class("PNGf")
    if data is not None:
        return data
    tiff = _read_macos_class("TIFF")
    if tiff is None:
        return None
    return _convert_to_png_via_sips(tiff, source_format="tiff")


def _read_macos_class(four_cc: str) -> bytes | None:
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    # «class XXXX» is the AppleScript four-char-code form. The chevrons are
    # part of the syntax — without them, "XXXX" is a plain string literal.
    cast_clause = f"\u00abclass {four_cc}\u00bb"
    script = (
        f'set targetFile to POSIX file "{tmp_path}"\n'
        "try\n"
        f"    set imgData to the clipboard as {cast_clause}\n"
        "on error\n"
        "    return\n"
        "end try\n"
        "set fh to open for access targetFile with write permission\n"
        "set eof of fh to 0\n"
        "write imgData to fh\n"
        "close access fh\n"
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=_READ_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0:
            return None
        data = tmp_path.read_bytes()
        return data if data else None
    finally:
        tmp_path.unlink(missing_ok=True)


def _convert_to_png_via_sips(data: bytes, *, source_format: str) -> bytes | None:
    if shutil.which("sips") is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=f".{source_format}", delete=False) as src:
        src_path = Path(src.name)
        src_path.write_bytes(data)
    out_path = src_path.with_suffix(".png")
    try:
        result = subprocess.run(
            ["sips", "-s", "format", "png", str(src_path), "--out", str(out_path)],
            capture_output=True,
            timeout=_READ_TIMEOUT_S,
            check=False,
        )
        if result.returncode != 0 or not out_path.is_file():
            return None
        return out_path.read_bytes() or None
    finally:
        src_path.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)


async def handle_clipboard_image_paste(
    app: VibeApp, *, notify_when_empty: bool
) -> None:
    if not is_clipboard_image_paste_supported():
        # Silently no-op: keybinding and slash command are hidden on
        # unsupported platforms so the user is never invited to use them.
        return
    data = await asyncio.to_thread(read_clipboard_image)
    if data is None:
        if notify_when_empty:
            app.notify(
                "No image found on the clipboard.", severity="warning", timeout=3
            )
        return
    if len(data) > MAX_IMAGE_BYTES:
        app.notify(
            f"Clipboard image is {naturalsize(len(data), binary=True)}; "
            f"max is {naturalsize(MAX_IMAGE_BYTES, binary=True)}.",
            severity="warning",
        )
        return
    try:
        active_model = app.agent_loop.config.get_active_model()
    except ValueError:
        active_model = None
    if active_model is not None and not active_model.supports_images:
        app.notify(
            f"Model `{active_model.alias}` does not support images. "
            f"Switch with /model or ask me to enable image support for this model.",
            severity="warning",
        )
        return
    try:
        path = write_clipboard_image(
            data, session_dir=app.agent_loop.session_logger.session_dir
        )
    except OSError as e:
        logger.warning("Failed to write pasted clipboard image: %r", e)
        app.notify("Failed to save pasted image to disk.", severity="warning")
        return
    if not insert_image_token_at_cursor(app, path):
        try:
            path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Failed to remove unused pasted clipboard image: %r", e)
        app.notify("Failed to paste image into prompt.", severity="warning")
        return
    app.notify(
        f"Image pasted as {path.name} ({naturalsize(len(data), binary=True)})",
        severity="information",
        timeout=2,
    )


def write_clipboard_image(data: bytes, *, session_dir: Path | None) -> Path:
    if session_dir is not None:
        target_dir = session_dir / "attachments"
    else:
        target_dir = Path(tempfile.gettempdir()) / "vibe-pasted-images"
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = target_dir / f"clipboard-{timestamp}.png"
    # Same-second collision: append a short numeric suffix until free.
    if path.exists():
        for n in range(1, _MAX_SAME_SECOND_COLLISIONS):
            candidate = target_dir / f"clipboard-{timestamp}-{n}.png"
            if not candidate.exists():
                path = candidate
                break
    path.write_bytes(data)
    return path


def insert_image_token_at_cursor(app: VibeApp, path: Path) -> bool:
    try:
        text_area = app.query_one(ChatTextArea)
    except Exception:
        return False
    token = f"@{path}" if " " not in str(path) else f"@'{path}'"
    current = text_area.text
    offset = text_area.get_cursor_offset()
    prev_char = current[offset - 1] if offset > 0 else ""
    prefix = "" if not prev_char or prev_char.isspace() else " "
    text_area.insert(f"{prefix}{token} ")
    return True


# Re-exports for testing without touching private names from outside.
__all__ = [
    "handle_clipboard_image_paste",
    "insert_image_token_at_cursor",
    "is_clipboard_image_paste_supported",
    "read_clipboard_image",
    "write_clipboard_image",
]
