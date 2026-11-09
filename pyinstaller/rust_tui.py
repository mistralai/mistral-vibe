"""Build-time helper: the vibe-rs binary `vibe-acp --setup` runs the wizard with."""

from __future__ import annotations

from importlib import metadata
import os
from pathlib import Path
import sys

_TUI_NAME = "vibe-rs.exe" if sys.platform == "win32" else "vibe-rs"
_TUI_WHEEL_ENTRY = f"vibe/_bin/{_TUI_NAME}"
_DISTRIBUTION = "mistral-vibe"
# A source build points here at its own cargo output; a wheel install carries it.
OVERRIDE_ENV = "VIBE_ACP_RUST_TUI"


class RustTuiRequiredError(SystemExit):
    """The build cannot produce vibe-acp without the setup wizard binary."""


def _installed_tui() -> Path | None:
    # Only a file the installed wheel's RECORD lists: a source tree's
    # vibe/_bin can hold a stale binary from an earlier wheel build.
    try:
        files = metadata.files(_DISTRIBUTION) or []
    except metadata.PackageNotFoundError:
        return None
    for file in files:
        if file.as_posix() == _TUI_WHEEL_ENTRY:
            return Path(str(file.locate()))
    return None


def collect_rust_tui() -> list[tuple[str, str]]:
    if os.environ.get("VIBE_SKIP_RUST_TUI"):
        return []
    override = os.environ.get(OVERRIDE_ENV)
    binary = Path(override) if override else _installed_tui()
    if binary is None or not binary.is_file():
        location = binary or f"no installed {_DISTRIBUTION} wheel"
        raise RustTuiRequiredError(
            f"vibe-acp bundles vibe-rs for `--setup`, but it was not found "
            f"({location}). Install the combined wheel, set {OVERRIDE_ENV} to a "
            f"built vibe-rs, or set VIBE_SKIP_RUST_TUI=1 for a bundle without "
            f"the wizard."
        )
    return [(str(binary), "vibe/_bin")]
