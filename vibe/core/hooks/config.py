from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.config import VibeConfig
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.hooks.models import HookConfig, HookConfigIssue, HookConfigResult
from vibe.core.utils.io import read_safe


class _HooksTomlRoot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    hooks: list[Any] = Field(default_factory=list)


def _format_validation_error(
    err: ValidationError, *, root_label: str = "config"
) -> str:
    """Single-line, human-readable summary (avoids pydantic's multi-line str(err) dump)."""
    parts: list[str] = []
    for item in err.errors():
        loc = item.get("loc") or ()
        loc_s = ".".join(str(x) for x in loc) if loc else root_label
        parts.append(f"{loc_s}: {item.get('msg', 'invalid')}")
    return " ; ".join(parts)


def _hook_entry_label(entry: Any, index: int) -> str:
    if isinstance(entry, dict) and (name := entry.get("name")):
        return str(name)
    return f"hooks[{index}]"


def _load_hooks_file(path: Path) -> HookConfigResult:
    hooks: list[HookConfig] = []
    issues: list[HookConfigIssue] = []

    if not path.is_file():
        return HookConfigResult(hooks=hooks, issues=issues)

    try:
        text = read_safe(path).text
        data = tomllib.loads(text)
    except (OSError, tomllib.TOMLDecodeError) as e:
        issues.append(HookConfigIssue(file=path, message=f"Failed to parse: {e}"))
        return HookConfigResult(hooks=hooks, issues=issues)

    try:
        root = _HooksTomlRoot.model_validate(data)
    except ValidationError as e:
        issues.append(
            HookConfigIssue(
                file=path, message=_format_validation_error(e, root_label="hooks file")
            )
        )
        return HookConfigResult(hooks=hooks, issues=issues)

    for i, entry in enumerate(root.hooks):
        try:
            hooks.append(HookConfig.model_validate(entry))
        except ValidationError as e:
            label = _hook_entry_label(entry, i)
            issues.append(
                HookConfigIssue(
                    file=path,
                    message=f"{label} - {_format_validation_error(e, root_label='hook')}",
                )
            )

    return HookConfigResult(hooks=hooks, issues=issues)


def load_hooks_from_fs(config: VibeConfig) -> HookConfigResult:
    if not config.enable_experimental_hooks:
        return HookConfigResult(hooks=[], issues=[])

    all_hooks: list[HookConfig] = []
    all_issues: list[HookConfigIssue] = []
    seen_names: set[str] = set()
    mgr = get_harness_files_manager()

    for path in mgr.hook_files:
        result = _load_hooks_file(path)
        all_issues.extend(result.issues)
        for hook in result.hooks:
            if hook.name in seen_names:
                all_issues.append(
                    HookConfigIssue(
                        file=path, message=f"Duplicate hook name: {hook.name!r}"
                    )
                )
                continue
            seen_names.add(hook.name)
            all_hooks.append(hook)

    return HookConfigResult(hooks=all_hooks, issues=all_issues)
