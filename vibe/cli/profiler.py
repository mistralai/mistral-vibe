"""General-purpose profiler for measuring any section of the application.

Wraps pyinstrument (dev-only dependency). Silently no-ops when not installed.
Activated by the VIBE_PROFILE=1 environment variable.

Usage:
    from vibe.cli import profiler

    profiler.start("startup")
    # ... code to profile ...
    profiler.stop_and_print()
"""

from __future__ import annotations

import dataclasses
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyinstrument import Profiler


@dataclasses.dataclass
class _State:
    profiler: Profiler | None = None
    label: str = "default"


_state = _State()


def is_enabled() -> bool:
    """Return True when profiling is activated via environment variable."""
    return bool(os.environ.get("VIBE_PROFILE"))


def start(label: str = "default") -> None:
    """Start profiling. The label is used to name the output file.

    No-op if pyinstrument is missing or env var unset.
    """
    if not is_enabled():
        return
    try:
        from pyinstrument import Profiler
    except ImportError:
        return

    if _state.profiler is not None:
        import warnings

        warnings.warn(
            "Profiler already running; stop it before starting a new one.", stacklevel=2
        )
        return

    _state.label = label
    _state.profiler = Profiler()
    _state.profiler.start()


def stop_and_print() -> None:
    """Stop profiling, write an HTML report, and print a text summary to stderr."""
    if _state.profiler is None:
        return
    _state.profiler.stop()

    from pathlib import Path
    import sys

    output_path = Path(f"{_state.label}-profile.html")
    output_path.write_text(_state.profiler.output_html(), encoding="utf-8")

    text_path = Path(f"{_state.label}-profile.txt")
    text_path.write_text(_state.profiler.output_text(color=False), encoding="utf-8")

    print(
        f"\n[profiler:{_state.label}] Saved HTML profile to {output_path.resolve()}",
        file=sys.stderr,
    )
    print(
        f"[profiler:{_state.label}] Saved text profile to {text_path.resolve()}",
        file=sys.stderr,
    )
    print(_state.profiler.output_text(color=True), file=sys.stderr)

    _state.profiler = None
    _state.label = "default"
