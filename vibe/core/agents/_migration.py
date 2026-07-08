from __future__ import annotations

from collections.abc import Iterable
import os
from pathlib import Path
import tempfile
import tomllib
from typing import Any

import tomli_w

from vibe.core.logger import logger

LEGACY_BASE_DISABLED_KEY = "base_disabled"


def migrate_agent_profile_config(data: dict[str, Any]) -> bool:
    legacy_disabled = data.get(LEGACY_BASE_DISABLED_KEY)
    if not isinstance(legacy_disabled, list):
        return False

    data.pop(LEGACY_BASE_DISABLED_KEY)
    disabled_tools = data.get("disabled_tools")
    if isinstance(disabled_tools, list):
        data["disabled_tools"] = list(
            dict.fromkeys([*disabled_tools, *legacy_disabled])
        )
    else:
        data["disabled_tools"] = legacy_disabled
    return True


def migrate_agent_profile_files(search_paths: Iterable[Path]) -> None:
    for directory in search_paths:
        try:
            _migrate_agent_profiles_in_dir(directory)
        except Exception as exc:
            logger.warning(
                "Failed to migrate agent profiles in %s", directory, exc_info=exc
            )


def _migrate_agent_profiles_in_dir(directory: Path) -> None:
    if not directory.is_dir():
        return

    for path in directory.glob("*.toml"):
        if not path.is_file():
            continue
        try:
            _migrate_agent_profile_file(path)
        except Exception as exc:
            logger.warning("Failed to migrate agent profile %s", path, exc_info=exc)


def _migrate_agent_profile_file(path: Path) -> None:
    with path.open("rb") as file:
        data = tomllib.load(file)

    if not migrate_agent_profile_config(data):
        return

    _write_agent_profile(path, data)


def _write_agent_profile(path: Path, data: dict[str, Any]) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tomli_w.dump(data, tmp_file)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        tmp_path.replace(path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
