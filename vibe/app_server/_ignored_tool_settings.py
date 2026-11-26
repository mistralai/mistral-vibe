"""Files in the custom tool folders, which Vibe no longer reads."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from vibe.app_server.models import ConfigIssue

SPAWN_DESCRIPTION_OVERRIDE_IGNORED = (
    "This file overrides the description of the former `task` tool and has no "
    "effect: subagents are started with the built-in `agents.spawn` tool, whose "
    "description cannot be overridden. Put guidance on when to delegate in "
    "AGENTS.md or in the agent's instructions instead."
)


def custom_tool_unsupported(name: str) -> str:
    return (
        f"Custom Python tool `{name}` is no longer supported and was not loaded. "
        "Ask Vibe to migrate it to a skill."
    )


def ignored_tool_settings(tool_dirs: Iterable[Path]) -> list[ConfigIssue]:
    """One issue per file in ``~/.vibe/tools/`` or ``.vibe/tools/`` that Vibe skips.

    Tool settings without effect, such as [tools.grep], are config warnings
    (vibe.core.config._setting_keys), which strict_config refuses.
    """
    issues: list[ConfigIssue] = []
    for tool_dir in tool_dirs:
        # Listed, never imported: importing a module runs its code.
        issues.extend(
            ConfigIssue(
                file=str(tool_file), message=custom_tool_unsupported(tool_file.stem)
            )
            for tool_file in sorted(tool_dir.glob("*.py"))
            if not tool_file.name.startswith("_")
        )
        override = tool_dir / "prompts" / "task.md"
        if override.is_file():
            issues.append(
                ConfigIssue(
                    file=str(override), message=SPAWN_DESCRIPTION_OVERRIDE_IGNORED
                )
            )
    return issues


__all__ = [
    "SPAWN_DESCRIPTION_OVERRIDE_IGNORED",
    "custom_tool_unsupported",
    "ignored_tool_settings",
]
