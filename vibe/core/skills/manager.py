from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
import threading
from types import MappingProxyType
from typing import TYPE_CHECKING

from cachetools import LRUCache

from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.skills.models import (
    REGISTRY_LATEST_ALIAS,
    InstalledSkill,
    RegistryRef,
    SkillConfigIssue,
    SkillInfo,
    SkillMetadata,
    SkillScope,
    SkillSource,
)
from vibe.core.skills.parser import (
    SkillParseError,
    load_openai_skill_metadata,
    openai_skill_metadata_path,
    parse_skill_markdown,
)
from vibe.core.skills.registry import _manifest, _resolved, _store
from vibe.core.skills.registry.models import pinned_name
from vibe.core.utils import name_matches
from vibe.observability.logging import logger
from vibe.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


@dataclass(frozen=True, slots=True)
class _ParsedSkillFile:
    metadata: SkillMetadata
    prompt: str
    openai_allows_implicit_invocation: bool
    issues: tuple[SkillConfigIssue, ...]


type _FileStamp = tuple[int, int, int] | None

# Every derivation of every session in the process re-discovers skills, and
# parsing frontmatter dominates that cost. Keyed by path and invalidated by the
# stat of both files a parse reads, so an edited skill is re-parsed on the next
# discovery. Bounded because each worktree brings its own project skill paths.
_parsed_skill_files: LRUCache[
    Path, tuple[tuple[_FileStamp, _FileStamp], _ParsedSkillFile]
] = LRUCache(maxsize=1024)
_parsed_skill_files_lock = threading.Lock()


def _file_stamp(path: Path) -> _FileStamp:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


def _parse_skill_file(skill_path: Path) -> _ParsedSkillFile:
    stamp = (
        _file_stamp(skill_path),
        _file_stamp(openai_skill_metadata_path(skill_path)),
    )
    with _parsed_skill_files_lock:
        cached = _parsed_skill_files.get(skill_path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    parsed = _read_skill_file(skill_path)
    with _parsed_skill_files_lock:
        _parsed_skill_files[skill_path] = (stamp, parsed)
    return parsed


def _read_skill_file(skill_path: Path) -> _ParsedSkillFile:
    try:
        content = read_safe(skill_path).text
    except OSError as e:
        raise SkillParseError(f"Cannot read file: {e}") from e

    frontmatter, body = parse_skill_markdown(content)
    metadata = SkillMetadata.model_validate(frontmatter)

    issues: tuple[SkillConfigIssue, ...] = ()
    try:
        openai_metadata = load_openai_skill_metadata(skill_path)
    except SkillParseError as e:
        metadata_path = openai_skill_metadata_path(skill_path)
        logger.warning("Failed to parse skill metadata at %s: %s", metadata_path, e)
        allows_implicit_invocation = False
        issues = (
            SkillConfigIssue(
                file=metadata_path, message=f"Model invocation disabled: {e}"
            ),
        )
    else:
        allows_implicit_invocation = (
            openai_metadata is None or openai_metadata.allows_implicit_invocation
        )
    return _ParsedSkillFile(
        metadata=metadata,
        prompt=body.strip(),
        openai_allows_implicit_invocation=allows_implicit_invocation,
        issues=issues,
    )


def _origin(skill: SkillInfo) -> str:
    if skill.skill_path is None:
        return f"the {skill.source.value} skills"
    return f"{skill.scope.value} {skill.source.value} {skill.skill_path}"


@dataclass(frozen=True, slots=True)
class ShadowedSkill:
    message: str
    args: tuple[str, ...]


def _shadowed(loser: SkillInfo, winner: SkillInfo) -> ShadowedSkill | None:
    """None for a file claimed twice under one name, which changes no skill a session
    loads. Claimed under two names, it loads once, under the winner's.
    """
    same_file = loser.skill_path is not None and loser.skill_path == winner.skill_path
    if same_file and loser.name == winner.name:
        return None
    if same_file:
        return ShadowedSkill(
            "Skill '%s' from %s is shadowed by '%s', which loads the same file",
            (loser.name, _origin(loser), winner.name),
        )
    return ShadowedSkill(
        "Skill '%s' from %s is shadowed by the one from %s",
        (loser.name, _origin(loser), _origin(winner)),
    )


def _named_as_pinned(skill: SkillInfo, name: str) -> SkillInfo:
    """The stored ``SKILL.md`` keeps the name the version had when it was first written,
    which an upstream rename or another repository's pin can change.
    """
    named = pinned_name(name, skill.name)
    if named != name:
        logger.debug(
            "Registry pin '%s' is not a valid skill name; it loads as '%s'", name, named
        )
    if named == skill.name:
        return skill
    return skill.model_copy(update={"name": named})


def _file_claimant(
    info: SkillInfo, by_file: Mapping[Path, SkillInfo]
) -> SkillInfo | None:
    if info.registry is None or info.skill_path is None:
        return None
    return by_file.get(info.skill_path)


class SkillManager:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfigSchema],
        *,
        harness_files: HarnessFilesManager | None = None,
        project_skills: Mapping[str, SkillInfo] | None = None,
    ) -> None:
        """``project_skills``, read in a sandbox, rank first among the project's, ahead
        of the files the host reads for it.
        """
        self._config_getter = config_getter
        self._harness_files = harness_files or get_harness_files_manager()
        self._project_skills = tuple((project_skills or {}).values())
        self._search_paths = self._compute_search_paths(self._config)
        self._config_issues: list[SkillConfigIssue] = []
        self._shadowed: list[ShadowedSkill] = []
        self._claimants = list(self._find_claimants())
        self._discovered = self._discover_skills()
        self.available_skills: Mapping[str, SkillInfo] = MappingProxyType(
            self._apply_filters(self._discovered)
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

    @property
    def shadowed(self) -> tuple[ShadowedSkill, ...]:
        """Only under the scope-first rule; without it each one is a debug line."""
        return tuple(self._shadowed)

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
        """A registry pin whose stored file an earlier claimant already loads is
        shadowed by it too, as a session's projection loads each file once.
        """
        loudly = self._config.experimental_enable_registry_skills
        skills: dict[str, SkillInfo] = {}
        by_file: dict[Path, SkillInfo] = {}
        for info in self._claimants:
            winner = skills.get(info.name) or _file_claimant(info, by_file)
            if winner is not None:
                self._report_shadowed(info, winner, loudly=loudly)
                continue
            skills[info.name] = info
            if info.skill_path is not None:
                by_file.setdefault(info.skill_path, info)
        return skills

    def _report_shadowed(
        self, loser: SkillInfo, winner: SkillInfo, *, loudly: bool
    ) -> None:
        shadow = _shadowed(loser, winner)
        if shadow is None:
            return
        if loudly:
            self._shadowed.append(shadow)
            return
        logger.debug(shadow.message, *shadow.args)

    def _find_claimants(self) -> Iterator[SkillInfo]:
        """A sandboxed session's own project skills come first among the project's in
        either order, right after ``skill_paths`` without registry skills.
        """
        if not self._config.experimental_enable_registry_skills:
            configured = {path.resolve() for path in self._config.skill_paths}
            yield from self._files(lambda base, _scope: base in configured)
            yield from self._project_skills
            yield from self._files(lambda base, _scope: base not in configured)
            return
        pins = self._registry_pins()
        yield from self._project_skills
        yield from self._files(lambda _base, scope: scope is SkillScope.PROJECT)
        yield from (pin for pin in pins if pin.scope is SkillScope.PROJECT)
        yield from self._files(lambda _base, scope: scope is SkillScope.GLOBAL)
        yield from (pin for pin in pins if pin.scope is SkillScope.GLOBAL)

    def _files(self, keep: Callable[[Path, SkillScope], bool]) -> Iterator[SkillInfo]:
        for base, scope in self._search_paths:
            if keep(base, scope) and base.is_dir():
                yield from self._discover_skills_in_dir(base, scope)

    def _discover_skills_in_dir(self, base: Path, scope: SkillScope) -> list[SkillInfo]:
        skills: list[SkillInfo] = []
        for skill_dir in base.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.is_file():
                continue
            skill_info = self._try_load_skill(
                skill_file, source=SkillSource.LOCAL, scope=scope
            )
            if skill_info is not None:
                skills.append(skill_info)
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

    def _registry_pins(self) -> list[SkillInfo]:
        return [
            info
            for path, scope in self._registry_sources()
            for entry in _manifest.load_sync(path).skills
            if (info := self._load_registry_entry(entry, scope)) is not None
        ]

    def installed_skills(self) -> list[InstalledSkill]:
        """Everything installed, one entry per (name, scope, source), in the order
        names resolve.

        Not de-duped by name: a skill pinned both globally and in the project
        is two pins the user can manage separately, and a pin shadowed by a
        local file of the same name is still a manifest entry they can remove.
        Each entry says whether its name resolves to it.

        Skills matched by ``disabled_skills`` are kept so one turned off can
        be turned back on.

        Reads the discovery done at construction rather than re-walking the
        search paths.
        """
        out: dict[tuple[str, SkillScope, SkillSource], InstalledSkill] = {}
        for info in self._claimants:
            key = (info.name, info.scope, info.source)
            if key not in out:
                out[key] = InstalledSkill(info, self._discovered.get(info.name) is info)
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
        loaded = self._try_load_skill(
            skill_file,
            source=SkillSource.REGISTRY,
            scope=scope,
            registry=RegistryRef(
                skill_id=entry.skill_id, version=version, alias=entry.alias
            ),
            check_dir_name=False,
        )
        return None if loaded is None else _named_as_pinned(loaded, entry.name)

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
            parsed = _parse_skill_file(skill_file)
            skill_name_from_dir = skill_file.parent.name
            if check_dir_name and parsed.metadata.name != skill_name_from_dir:
                logger.warning(
                    "Skill name '%s' doesn't match directory name '%s' at %s",
                    parsed.metadata.name,
                    skill_name_from_dir,
                    skill_file,
                )
            self._config_issues.extend(parsed.issues)
            return SkillInfo.from_metadata(
                parsed.metadata,
                skill_file,
                prompt=parsed.prompt,
                source=source,
                scope=scope,
                registry=registry,
                model_invocable=parsed.openai_allows_implicit_invocation,
            )
        except Exception as e:
            logger.warning("Failed to parse skill at %s: %s", skill_file, e)
            self._config_issues.append(
                SkillConfigIssue(file=skill_file, message=f"Failed to load: {e}")
            )
            return None

    @property
    def custom_skills_count(self) -> int:
        return len(self.available_skills)

    def get_skill(self, name: str) -> SkillInfo | None:
        return self.available_skills.get(name)
