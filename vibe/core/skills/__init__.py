from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.skills.manager import SkillManager
    from vibe.core.skills.models import SkillConfigIssue, SkillInfo, SkillMetadata
    from vibe.core.skills.parser import SkillParseError

__all__ = [
    "SkillConfigIssue",
    "SkillInfo",
    "SkillManager",
    "SkillMetadata",
    "SkillParseError",
]

_MAPPING: dict[str, tuple[str, str]] = {
    "SkillManager": ("vibe.core.skills.manager", "SkillManager"),
    "SkillConfigIssue": ("vibe.core.skills.models", "SkillConfigIssue"),
    "SkillInfo": ("vibe.core.skills.models", "SkillInfo"),
    "SkillMetadata": ("vibe.core.skills.models", "SkillMetadata"),
    "SkillParseError": ("vibe.core.skills.parser", "SkillParseError"),
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
