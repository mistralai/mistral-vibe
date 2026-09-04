from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.skills.builtins import BUILTIN_SKILLS
from vibe.core.skills.models import (
    REGISTRY_LATEST_ALIAS,
    ParsedSkillCommand,
    RegistryRef,
    SkillConfigIssue,
    SkillInfo,
    SkillMetadata,
    SkillScope,
    SkillSource,
)
from vibe.core.skills.parser import SkillParseError, parse_skill_markdown
from vibe.core.skills.registry import _manifest, _resolved, _store
from vibe.core.utils import name_matches
from vibe.observability.logging import logger
from vibe.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


class SkillManager:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfigSchema],
        *,
        harness_files: HarnessFilesManager | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._harness_files = harness_files or get_harness_files_manager()
        self._search_paths = self._compute_search_paths(self._config)
        self._config_issues: list[SkillConfigIssue] = []
        self.available_skills: Mapping[str, SkillInfo] = MappingProxyType(
            self._apply_filters(self._discover_skills())
        )

        if self.available_skills:
            logger.info(
                "Discovered %d skill(s) from %d search path(s)",
                len(self.available_skills),
                len(self._search_paths),
            )

    @property
    def _config(self) -> VibeConfigSchema:
        return self._config_getter()

    @property
    def config_issues(self) -> tuple[SkillConfigIssue, ...]:
        return tuple(self._config_issues)

    def _apply_filters(self, skills: dict[str, SkillInfo]) -> dict[str, SkillInfo]:
        if self._config.enabled_skills:
            return {
                name: info
                for name, info in skills.items()
                if name_matches(name, self._config.enabled_skills)
            }
        if self._config.disabled_skills:
            return {
                name: info
                for name, info in skills.items()
                if not name_matches(name, self._config.disabled_skills)
            }
        return dict(skills)

    def _compute_search_paths(
        self, config: VibeConfigSchema
    ) -> list[tuple[Path, SkillScope]]:
        paths: list[tuple[Path, SkillScope]] = []

        for path in config.skill_paths:
            if path.is_dir():
                paths.append((path, SkillScope.GLOBAL))

        mgr = self._harness_files
        paths.extend((p, SkillScope.PROJECT) for p in mgr.project_skills_dirs)
        paths.extend((p, SkillScope.GLOBAL) for p in mgr.user_skills_dirs)

        global_paths = {p.resolve() for p in mgr.user_skills_dirs}
        global_paths.update(
            p.resolve() for p, scope in paths if scope is SkillScope.GLOBAL
        )

        unique: list[tuple[Path, SkillScope]] = []
        seen: set[Path] = set()
        for p, scope in paths:
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            if scope is SkillScope.PROJECT and rp in global_paths:
                scope = SkillScope.GLOBAL
            unique.append((rp, scope))

        return unique

    def _discover_skills(self) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {**BUILTIN_SKILLS}
        for base, scope in self._search_paths:
            if not base.is_dir():
                continue
            for name, info in self._discover_skills_in_dir(base, scope).items():
                if name not in skills:
                    skills[name] = info
                else:
                    logger.debug(
                        "Skipping duplicate skill '%s' at %s (already loaded from %s)",
                        name,
                        info.skill_path,
                        skills[name].skill_path,
                    )
        for name, info in self._discover_registry_skills().items():
            if name not in skills:
                skills[name] = info
            else:
                logger.debug(
                    "Skipping registry skill '%s'; a local/builtin skill wins", name
                )
        return skills

    def _discover_skills_in_dir(
        self, base: Path, scope: SkillScope
    ) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            skill_info = self._try_load_skill(
                skill_file, source=SkillSource.LOCAL, scope=scope
            )
            if skill_info is None:
                continue
            if skill_info.name in BUILTIN_SKILLS:
                logger.debug(
                    "Skipping skill '%s' at %s because builtin skill names are reserved",
                    skill_info.name,
                    skill_info.skill_path,
                )
                continue
            if skill_info.name in skills:
                logger.debug(
                    "Skipping duplicate skill '%s' at %s (already loaded from %s)",
                    skill_info.name,
                    skill_info.skill_path,
                    skills[skill_info.name].skill_path,
                )
                continue
            skills[skill_info.name] = skill_info
        return skills

    def _registry_sources(self) -> list[tuple[Path, SkillScope]]:
        sources: list[tuple[Path, SkillScope]] = [
            (_manifest.global_manifest_path(), SkillScope.GLOBAL)
        ]
        sources.extend(
            (p, SkillScope.PROJECT)
            for p in _manifest.project_manifest_paths_sync(
                self._harness_files.project_roots
            )
        )
        return sources

    def _discover_registry_skills(self) -> dict[str, SkillInfo]:
        """The active set the agent uses: one entry per name, project wins."""
        if not self._config.experimental_enable_registry_skills:
            return {}
        out: dict[str, SkillInfo] = {}
        for path, scope in self._registry_sources():
            for entry in _manifest.load_sync(path).skills:
                info = self._load_registry_entry(entry, scope)
                if info is not None:
                    out[entry.name] = info
        return out

    def registry_pins(self) -> list[SkillInfo]:
        """Every registry pin as its own SkillInfo, one per (name, scope).

        Unlike ``available_skills`` (de-duped, project-wins) this keeps a global
        and a project pin of the same skill as separate rows, for the browser.
        """
        if not self._config.experimental_enable_registry_skills:
            return []
        out: dict[tuple[str, SkillScope], SkillInfo] = {}
        for path, scope in self._registry_sources():
            for entry in _manifest.load_sync(path).skills:
                info = self._load_registry_entry(entry, scope)
                if info is not None:
                    out[(entry.name, scope)] = info
        return list(out.values())

    def _load_registry_entry(
        self, entry: _manifest.ManifestEntry, scope: SkillScope
    ) -> SkillInfo | None:
        if not _store.is_safe_skill_id(entry.skill_id):
            logger.warning(
                "Skipping registry skill with unsafe id '%s'", entry.skill_id
            )
            return None
        if isinstance(entry.version, int):
            version: int | None = entry.version
        elif entry.version == REGISTRY_LATEST_ALIAS:
            version = _store.latest_materialized_sync(entry.skill_id)
        else:
            version = _resolved.get(entry.skill_id, entry.version)
            if version is None or not _store.is_materialized_sync(
                entry.skill_id, version
            ):
                version = _store.latest_materialized_sync(entry.skill_id)
        if version is None:
            logger.debug(
                "Registry skill '%s' (%s@%s) not materialized; skipping",
                entry.name,
                entry.skill_id,
                entry.version,
            )
            return None
        skill_file = _store.skill_dir(entry.skill_id, version) / "SKILL.md"
        if not skill_file.is_file():
            logger.debug(
                "Registry skill '%s' (%s@%d) not materialized; skipping",
                entry.name,
                entry.skill_id,
                version,
            )
            return None
        return self._try_load_skill(
            skill_file,
            source=SkillSource.REGISTRY,
            scope=scope,
            registry=RegistryRef(
                skill_id=entry.skill_id, version=version, alias=entry.alias
            ),
            check_dir_name=False,
        )

    def _try_load_skill(
        self,
        skill_file: Path,
        *,
        source: SkillSource,
        scope: SkillScope,
        registry: RegistryRef | None = None,
        check_dir_name: bool = True,
    ) -> SkillInfo | None:
        try:
            skill_info = self._parse_skill_file(
                skill_file,
                source=source,
                scope=scope,
                registry=registry,
                check_dir_name=check_dir_name,
            )
        except Exception as e:
            logger.warning("Failed to parse skill at %s: %s", skill_file, e)
            self._config_issues.append(
                SkillConfigIssue(file=skill_file, message=f"Failed to load: {e}")
            )
            return None
        return skill_info

    def _parse_skill_file(
        self,
        skill_path: Path,
        *,
        source: SkillSource,
        scope: SkillScope,
        registry: RegistryRef | None = None,
        check_dir_name: bool = True,
    ) -> SkillInfo:
        try:
            content = read_safe(skill_path).text
        except OSError as e:
            raise SkillParseError(f"Cannot read file: {e}") from e

        frontmatter, body = parse_skill_markdown(content)
        metadata = SkillMetadata.model_validate(frontmatter)

        if check_dir_name:
            skill_name_from_dir = skill_path.parent.name
            if metadata.name != skill_name_from_dir:
                logger.warning(
                    "Skill name '%s' doesn't match directory name '%s' at %s",
                    metadata.name,
                    skill_name_from_dir,
                    skill_path,
                )

        return SkillInfo.from_metadata(
            metadata,
            skill_path,
            prompt=body.strip(),
            source=source,
            scope=scope,
            registry=registry,
        )

    @property
    def custom_skills_count(self) -> int:
        return sum(name not in BUILTIN_SKILLS for name in self.available_skills)

    def get_skill(self, name: str) -> SkillInfo | None:
        return self.available_skills.get(name)

    def parse_skill_command(self, text_prompt: str) -> ParsedSkillCommand | None:
        stripped = text_prompt.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped[1:].split(None, 1)
        if not parts:
            return None

        skill_name = parts[0].lower()
        skill_info = self.get_skill(skill_name)
        if skill_info is None or not skill_info.user_invocable:
            return None

        extra_instructions = parts[1] if len(parts) > 1 else None

        return ParsedSkillCommand(
            name=skill_name,
            content=skill_info.prompt,
            extra_instructions=extra_instructions,
        )
