from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.paths.config_paths import resolve_local_skills_dir
from vibe.core.paths.global_paths import GLOBAL_SKILLS_DIR
from vibe.core.skills.models import SkillInfo, SkillMetadata
from vibe.core.skills.parser import SkillParseError, parse_frontmatter
from vibe.core.utils import name_matches

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

logger = getLogger("vibe")


class SkillManager:
    def __init__(self, config_getter: Callable[[], VibeConfig]) -> None:
        self._config_getter = config_getter
        self._search_paths = self._compute_search_paths(self._config)
        self._available: dict[str, SkillInfo] = self._discover_skills()

        if self._available:
            logger.info(
                "Discovered %d skill(s) from %d search path(s)",
                len(self._available),
                len(self._search_paths),
            )

    @property
    def _config(self) -> VibeConfig:
        return self._config_getter()

    @property
    def available_skills(self) -> dict[str, SkillInfo]:
        if self._config.enabled_skills:
            return {
                name: info
                for name, info in self._available.items()
                if name_matches(name, self._config.enabled_skills)
            }
        if self._config.disabled_skills:
            return {
                name: info
                for name, info in self._available.items()
                if not name_matches(name, self._config.disabled_skills)
            }
        return dict(self._available)

    @staticmethod
    def _compute_search_paths(config: VibeConfig) -> list[Path]:
        paths: list[Path] = []

        for path in config.skill_paths:
            if path.is_dir():
                paths.append(path)

        if (skills_dir := resolve_local_skills_dir(Path.cwd())) is not None:
            paths.append(skills_dir)

        if GLOBAL_SKILLS_DIR.path.is_dir():
            paths.append(GLOBAL_SKILLS_DIR.path)

        unique: list[Path] = []
        for p in paths:
            rp = p.resolve()
            if rp not in unique:
                unique.append(rp)

        return unique

    def _discover_skills(self) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for base in self._search_paths:
            if not base.is_dir():
                continue
            for name, info in self._discover_skills_in_dir(base).items():
                if name not in skills:
                    skills[name] = info
                else:
                    logger.debug(
                        "Skipping duplicate skill '%s' at %s (already loaded from %s)",
                        name,
                        info.skill_path,
                        skills[name].skill_path,
                    )
        return skills

    def _discover_skills_in_dir(self, base: Path) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            if (skill_info := self._try_load_skill(skill_file)) is not None:
                skills[skill_info.name] = skill_info
        return skills

    def _try_load_skill(self, skill_file: Path) -> SkillInfo | None:
        try:
            skill_info = self._parse_skill_file(skill_file)
        except Exception as e:
            logger.warning("Failed to parse skill at %s: %s", skill_file, e)
            return None
        return skill_info

    def _parse_skill_file(self, skill_path: Path) -> SkillInfo:
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError as e:
            raise SkillParseError(f"Cannot read file: {e}") from e

        frontmatter, _ = parse_frontmatter(content)
        metadata = SkillMetadata.model_validate(frontmatter)

        skill_name_from_dir = skill_path.parent.name
        if metadata.name != skill_name_from_dir:
            logger.warning(
                "Skill name '%s' doesn't match directory name '%s' at %s",
                metadata.name,
                skill_name_from_dir,
                skill_path,
            )

        return SkillInfo.from_metadata(metadata, skill_path)

    def get_skill(self, name: str) -> SkillInfo | None:
        return self.available_skills.get(name)
