"""Tool settings a config can still carry that no tool reads any more."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.app_server.models import ConfigIssue
from vibe.core.paths import DEFAULT_TOOL_DIR

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

GREP_SETTINGS_IGNORED = (
    "[tools.grep] has no effect: there is no grep tool, and the agent searches "
    "through the shell. Commands such as `rg` and `grep` follow your shell tool's "
    "settings, such as [tools.bash]."
)
SPAWN_DESCRIPTION_OVERRIDE_IGNORED = (
    "This file overrides the description of the former `task` tool and has no "
    "effect: subagents are started with the built-in `agents.spawn` tool, whose "
    "description cannot be overridden. Put guidance on when to delegate in "
    "AGENTS.md or in the agent's instructions instead."
)


def ignored_tool_settings(
    config: VibeConfigSchema, tool_search_paths: Iterable[Path]
) -> list[ConfigIssue]:
    issues: list[ConfigIssue] = []
    if "grep" in config.tools:
        issues.append(ConfigIssue(file="", message=GREP_SETTINGS_IGNORED))
    builtin_dir = DEFAULT_TOOL_DIR.path.resolve()
    for base in tool_search_paths:
        if base.resolve() == builtin_dir:
            continue
        override = base / "prompts" / "task.md"
        if override.is_file():
            issues.append(
                ConfigIssue(
                    file=str(override), message=SPAWN_DESCRIPTION_OVERRIDE_IGNORED
                )
            )
    return issues


__all__ = [
    "GREP_SETTINGS_IGNORED",
    "SPAWN_DESCRIPTION_OVERRIDE_IGNORED",
    "ignored_tool_settings",
]
