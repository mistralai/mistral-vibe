from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorManagerPort,
    NarratorState,
)

if TYPE_CHECKING:
    from vibe.cli.narrator_manager.narrator_manager import NarratorManager

__all__ = [
    "NarratorManager",
    "NarratorManagerListener",
    "NarratorManagerPort",
    "NarratorState",
]


def __getattr__(name: str) -> object:
    if name == "NarratorManager":
        from vibe.cli.narrator_manager.narrator_manager import NarratorManager

        return NarratorManager
    raise AttributeError(name)
