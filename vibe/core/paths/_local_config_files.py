from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


def dedup_paths(paths: Iterable[Path]) -> list[Path]:
    """Resolve and dedup paths, preserving first-occurrence order."""
    resolved = [p.resolve() for p in paths]
    return [p for i, p in enumerate(resolved) if p not in resolved[:i]]


_VIBE_DIR = Path(".vibe")
_TOOLS_SUBDIR = _VIBE_DIR / "tools"
_VIBE_SKILLS_SUBDIR = _VIBE_DIR / "skills"
_AGENTS_SUBDIR = _VIBE_DIR / "agents"
_AGENTS_DIR = Path(".agents")
_AGENTS_SKILLS_SUBDIR = _AGENTS_DIR / "skills"


@dataclass(frozen=True)
class LocalConfigDirs:
    """Local config directories discovered at a project root."""

    config_dirs: tuple[Path, ...] = ()
    tools: tuple[Path, ...] = ()
    skills: tuple[Path, ...] = ()
    agents: tuple[Path, ...] = ()

    def __or__(self, other: LocalConfigDirs) -> LocalConfigDirs:
        return LocalConfigDirs(
            config_dirs=tuple(dedup_paths([*self.config_dirs, *other.config_dirs])),
            tools=tuple(dedup_paths([*self.tools, *other.tools])),
            skills=tuple(dedup_paths([*self.skills, *other.skills])),
            agents=tuple(dedup_paths([*self.agents, *other.agents])),
        )


def find_local_config_dirs(root: Path) -> LocalConfigDirs:
    """Inspect *root* for ``.vibe/`` and ``.agents/`` config directories.

    Only the root itself is examined — no recursion into subdirectories.
    """
    resolved = root.resolve()
    config_dirs: list[Path] = []
    tools: list[Path] = []
    skills: list[Path] = []
    agents: list[Path] = []

    vibe_dir = resolved / _VIBE_DIR
    if vibe_dir.is_dir():
        has_content = False
        if (candidate := resolved / _TOOLS_SUBDIR).is_dir():
            tools.append(candidate)
            has_content = True
        if (candidate := resolved / _VIBE_SKILLS_SUBDIR).is_dir():
            skills.append(candidate)
            has_content = True
        if (candidate := resolved / _AGENTS_SUBDIR).is_dir():
            agents.append(candidate)
            has_content = True
        if (
            has_content
            or (vibe_dir / "prompts").is_dir()
            or (vibe_dir / "config.toml").is_file()
        ):
            config_dirs.append(vibe_dir)

    agents_dir = resolved / _AGENTS_DIR
    if agents_dir.is_dir() and (candidate := resolved / _AGENTS_SKILLS_SUBDIR).is_dir():
        skills.append(candidate)
        config_dirs.append(agents_dir)

    return LocalConfigDirs(
        config_dirs=tuple(config_dirs),
        tools=tuple(tools),
        skills=tuple(skills),
        agents=tuple(agents),
    )
