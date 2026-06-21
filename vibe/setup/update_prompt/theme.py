from __future__ import annotations

from collections.abc import Mapping
import os
from pathlib import Path
import tomllib
from typing import Any

from vibe.core.config import DEFAULT_THEME, resolve_theme_name
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.utils.io import read_safe


def _read_config_theme(config_file: Path) -> Any:
    try:
        config = tomllib.loads(read_safe(config_file, raise_on_error=True).text)
    except FileNotFoundError:
        return None
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        logger.debug(
            "Failed to read update prompt theme from %s", config_file, exc_info=exc
        )
        return None

    if not isinstance(config, dict):
        return None
    return config.get("theme")


def load_update_prompt_theme(
    *, environ: Mapping[str, str] | None = None, config_file: Path | None = None
) -> str:
    resolved_environ = os.environ if environ is None else environ
    if "VIBE_THEME" in resolved_environ:
        return resolve_theme_name(resolved_environ["VIBE_THEME"])

    resolved_config_file = config_file or get_harness_files_manager().config_file
    if resolved_config_file is None:
        return DEFAULT_THEME

    return resolve_theme_name(_read_config_theme(resolved_config_file))
