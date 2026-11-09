from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.config.harness_files._harness_manager import (
        FileSource,
        HarnessFilesManager,
        get_harness_files_manager,
        init_harness_files_manager,
        reset_harness_files_manager,
    )

__all__ = [
    "FileSource",
    "HarnessFilesManager",
    "get_harness_files_manager",
    "init_harness_files_manager",
    "reset_harness_files_manager",
]

_MAPPING: dict[str, tuple[str, str]] = {
    "FileSource": ("vibe.core.config.harness_files._harness_manager", "FileSource"),
    "HarnessFilesManager": (
        "vibe.core.config.harness_files._harness_manager",
        "HarnessFilesManager",
    ),
    "get_harness_files_manager": (
        "vibe.core.config.harness_files._harness_manager",
        "get_harness_files_manager",
    ),
    "init_harness_files_manager": (
        "vibe.core.config.harness_files._harness_manager",
        "init_harness_files_manager",
    ),
    "reset_harness_files_manager": (
        "vibe.core.config.harness_files._harness_manager",
        "reset_harness_files_manager",
    ),
}


def __getattr__(name: str) -> object:
    if name in _MAPPING:
        import importlib

        module_name, attr_name = _MAPPING[name]
        module = importlib.import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
