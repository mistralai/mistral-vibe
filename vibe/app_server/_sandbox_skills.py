"""Skills of a sandboxed session, copied into its sandbox.

When a session's tools run in a sandbox, the model reads a skill's files with
those tools, so the paths it is told have to exist there. Each skill read on
the host (a user skill, or a plugin's, the shipped ``vibe`` plugin's included)
is copied into the sandbox's temporary directory, outside the workspace, at
``<tmp>/mistralai-vibe-skills/<digest>/``: the digest covers every file's path
and content, so a copy is checked before it is reused, uploaded only when
missing, and shared by every session and run on the same sandbox. The project's
own skills already live in the sandbox and are read there.

The sandbox side runs as operations of the tool helper (``skills-scan`` and
``skills-install``). The digest, the size limits and the path rules are those
of the hosted Runtime's skill materialization, so the same skill gets the same
digest either way.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path, PurePosixPath
import posixpath
import stat
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from mistralai_vibe_local_harness.vibe import SandboxToolError, run_helper
from vibe.app_server.models import ConfigIssue
from vibe.core.plugins._native import plugin_skill_runtime_path
from vibe.core.skills.models import InstalledSkill, SkillInfo, SkillMetadata, SkillScope
from vibe.core.skills.parser import (
    SkillParseError,
    parse_openai_skill_metadata,
    parse_skill_markdown,
)
from vibe.core.tools.builtins.skill import (
    MAX_WALKED_SKILL_ENTRIES,
    SAMPLED_SKILL_FILES,
    SKIPPED_SKILL_DIR_NAMES,
)
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import RustPluginContextDefinition
    from mistralai_vibe_local_harness.vibe import SandboxAdapter

MAX_SKILL_MARKDOWN_BYTES = 2 * 1024 * 1024
MAX_SKILL_ASSET_COUNT = 200
MAX_SKILL_ASSET_BYTES = 256 * 1024
MAX_TOTAL_SKILL_ASSET_BYTES = 1024 * 1024
SKILLS_DIRNAME = "mistralai-vibe-skills"
"""The helper's directory for skill copies, in the sandbox's temp dir."""
_SKILL_ENTRY_FILE = "SKILL.md"
_PROJECT_SKILL_DIRS = (".vibe/skills", ".agents/skills")
_OPENAI_METADATA_MAX_BYTES = 64 * 1024
_TIMEOUT_SECONDS = 60.0
_COPY_FAILED = "copy-failed"


class _SkillCopyError(ValueError):
    pass


class _ProjectSkillItem(BaseModel):
    directory: str
    markdown: str | None
    openai: str | None
    files: list[str]
    error: str | None


class _Scan(BaseModel):
    root: str
    present: list[str]
    project: list[_ProjectSkillItem]


class _Install(BaseModel):
    root: str
    failures: dict[str, str]


@dataclass(frozen=True, slots=True)
class SandboxSkillLocation:
    """Where a skill lives in the sandbox, as the model is told it."""

    path: str
    """The sandbox path of its ``SKILL.md``."""
    base_dir: str
    files: tuple[str, ...]
    """The sample of its other files the skill tool lists."""


class SandboxSessionSkills:
    """What one sandboxed session takes from its sandbox's skills."""

    def __init__(
        self,
        sandbox: SandboxSkills,
        project_skills: Mapping[str, SkillInfo],
        issues: Iterable[ConfigIssue],
    ) -> None:
        self._sandbox = sandbox
        self.project_skills = project_skills
        """At their sandbox paths, not the host's."""
        self._opened = tuple(issues)
        self._left_out: tuple[ConfigIssue, ...] = ()

    @property
    def issues(self) -> tuple[ConfigIssue, ...]:
        """Includes the host skills left out by the latest :meth:`install`, not only
        those left out as the session opened.
        """
        return (*self._opened, *self._left_out)

    def locate(self, skill: SkillInfo) -> SandboxSkillLocation | None:
        return self._sandbox.locate(skill)

    async def install(self, skills: Iterable[SkillInfo]) -> None:
        """A skill the copy leaves out stays among the :attr:`issues` until the next
        copy. A failed copy is logged once until one succeeds, and the session goes on
        with the copies there are.
        """
        try:
            left_out = await self._sandbox.install(skills)
        except Exception:
            if self._sandbox.first_report(_COPY_FAILED):
                logger.exception("Failed to copy the session's skills into its sandbox")
            return
        self._sandbox.recovered(_COPY_FAILED)
        reported = {issue.file for issue in self._opened}
        self._left_out = tuple(
            issue for issue in left_out if issue.file not in reported
        )

    def copied_version(self, skill: SkillInfo, *, quiet: bool = False) -> SkillInfo:
        """An alias pin whose version has no copy keeps the newest older copy, as the
        same pin (name, scope and alias); logged once unless ``quiet``. A pin on a
        version number gets no stand-in, and a newer copy never stands in: the alias may
        have moved back.
        """
        registry = skill.registry
        if registry is None or registry.alias is None or self.locate(skill) is not None:
            return skill
        copied = self._sandbox.copied(registry.skill_id, registry.version)
        if copied is None or copied.registry is None:
            return skill
        kept = copied.registry.version
        report = ("kept", skill.name, kept, registry.version)
        if not quiet and self._sandbox.first_report(report):
            logger.warning(
                "Kept v%d of skill %r: v%d has no copy in the sandbox",
                kept,
                skill.name,
                registry.version,
            )
        version = registry.model_copy(update={"version": kept})
        return copied.model_copy(
            update={"name": skill.name, "scope": skill.scope, "registry": version}
        )

    def installed(self, skills: Iterable[InstalledSkill]) -> list[InstalledSkill]:
        """Each active one in the version :meth:`copied_version` keeps for it."""
        return [
            replace(skill, info=self.copied_version(skill.info, quiet=True))
            if skill.active
            else skill
            for skill in skills
        ]


@dataclass(frozen=True, slots=True)
class _SkillTree:
    digest: str
    files: tuple[tuple[str, bytes], ...]


class SandboxSkills:
    """The copies of host skills in one sandbox, shared by its sessions."""

    def __init__(self, sandbox: SandboxAdapter) -> None:
        self._sandbox = sandbox
        self._root: str | None = None
        # Digests checked or written in this process: never uploaded twice.
        self._present: set[str] = set()
        # By the host path of a skill's SKILL.md, or the sandbox path of a
        # project skill's.
        self._locations: dict[str, SandboxSkillLocation] = {}
        self._registry_copies: dict[str, dict[int, SkillInfo]] = {}
        self._reported: set[Hashable] = set()
        self._unlisted: str | None = None

    async def prepare_session(
        self, skills: Iterable[SkillInfo], *, roots: Sequence[Path]
    ) -> SandboxSessionSkills:
        """Copy ``skills`` into the sandbox and read the project skills of ``roots``.

        A skill that cannot be copied is left out, as is every skill without a
        copy from before when the sandbox cannot run the copy, and each one left
        out is a config issue. Raises :class:`SandboxUnavailableError` when the
        sandbox itself fails.
        """
        skills = list(skills)
        trees, issues = await self._read_trees(skills)
        scan = await self._scan(trees, roots=roots, issues=issues)
        if scan is None:
            return SandboxSessionSkills(self, {}, issues)
        await self._upload(trees, issues, skills)
        return SandboxSessionSkills(self, self._project_skills(scan, issues), issues)

    async def install(self, skills: Iterable[SkillInfo]) -> list[ConfigIssue]:
        """Copy ``skills`` into the sandbox, unless they are already there; the
        ones left out, each logged the first time.
        """
        skills = list(skills)
        trees, issues = await self._read_trees(skills)
        if self._root is None or any(
            tree.digest not in self._present for _, tree in trees
        ):
            if await self._scan(trees, roots=(), issues=issues) is None:
                return issues
        await self._upload(trees, issues, skills)
        return issues

    def locate(self, skill: SkillInfo) -> SandboxSkillLocation | None:
        """Where ``skill`` is in the sandbox, or None when it was not copied."""
        entry = plugin_skill_runtime_path(skill)
        return None if entry is None else self._locations.get(str(entry))

    def copied(self, skill_id: str, below: int) -> SkillInfo | None:
        copies = self._registry_copies.get(skill_id, {})
        older = [version for version in copies if version < below]
        return copies[max(older)] if older else None

    def first_report(self, key: Hashable) -> bool:
        """A problem every derivation meets again is logged once, until
        :meth:`recovered` says it went away.
        """
        if key in self._reported:
            return False
        self._reported.add(key)
        return True

    def recovered(self, key: Hashable) -> None:
        self._reported.discard(key)

    async def _read_trees(
        self, skills: Iterable[SkillInfo]
    ) -> tuple[list[tuple[str, _SkillTree]], list[ConfigIssue]]:
        trees, failures = await asyncio.to_thread(_read_trees, skills)
        return trees, [
            self._left_out(entry, f"it cannot be copied: {error}")
            for entry, error in failures
        ]

    def _left_out(self, entry: str, reason: str) -> ConfigIssue:
        if self.first_report((entry, reason)):
            logger.warning("Dropped skill %s: %s", entry, reason)
        return ConfigIssue(
            file=entry, message=f"Skill left out of the sandbox: {reason}"
        )

    async def _scan(
        self,
        trees: Sequence[tuple[str, _SkillTree]],
        *,
        roots: Sequence[Path],
        issues: list[ConfigIssue],
    ) -> _Scan | None:
        request = {
            "digests": sorted({tree.digest for _, tree in trees}),
            "roots": [str(root) for root in roots],
            "project_dirs": list(_PROJECT_SKILL_DIRS),
            "markdown_limit": MAX_SKILL_MARKDOWN_BYTES,
            "metadata_limit": _OPENAI_METADATA_MAX_BYTES,
            "sample": {
                "skipped_dirs": sorted(SKIPPED_SKILL_DIR_NAMES),
                "walk_limit": MAX_WALKED_SKILL_ENTRIES,
                "count": SAMPLED_SKILL_FILES,
            },
        }
        try:
            scan = _Scan.model_validate(await self._run("skills-scan", request))
        except (SandboxToolError, ValidationError) as error:
            if str(error) != self._unlisted:
                logger.warning(
                    "Skills are off for this sandboxed session: the sandbox could "
                    "not list them (%s)",
                    error,
                )
            self._unlisted = str(error)
            issues.extend(
                self._left_out(entry, f"the sandbox could not list skills: {error}")
                for entry, _ in trees
                if entry not in self._locations
            )
            return None
        self._root = scan.root
        self._present.update(scan.present)
        self._unlisted = None
        return scan

    async def _upload(
        self,
        trees: Sequence[tuple[str, _SkillTree]],
        issues: list[ConfigIssue],
        skills: Iterable[SkillInfo],
    ) -> None:
        assert self._root is not None
        pinned = _registry_skills_by_entry(skills)
        missing = {
            tree.digest: tree for _, tree in trees if tree.digest not in self._present
        }
        failures: dict[str, str] = {}
        if missing:
            request = {
                "skills": {
                    digest: [
                        [path, base64.urlsafe_b64encode(content).decode("ascii")]
                        for path, content in tree.files
                    ]
                    for digest, tree in missing.items()
                }
            }
            try:
                installed = _Install.model_validate(
                    await self._run("skills-install", request)
                )
            except (SandboxToolError, ValidationError) as error:
                failures = dict.fromkeys(missing, str(error))
            else:
                failures = installed.failures
            self._present.update(set(missing) - set(failures))
        for entry, tree in trees:
            if tree.digest in failures:
                issues.append(
                    self._left_out(
                        entry, f"it could not be copied: {failures[tree.digest]}"
                    )
                )
                continue
            base_dir = posixpath.join(self._root, tree.digest)
            self._locations[entry] = SandboxSkillLocation(
                path=posixpath.join(base_dir, _SKILL_ENTRY_FILE),
                base_dir=base_dir,
                files=_sample(path for path, _ in tree.files),
            )
            skill = pinned.get(entry)
            if skill is not None and skill.registry is not None:
                copies = self._registry_copies.setdefault(skill.registry.skill_id, {})
                copies[skill.registry.version] = skill

    def _project_skills(
        self, scan: _Scan, issues: list[ConfigIssue]
    ) -> dict[str, SkillInfo]:
        skills: dict[str, SkillInfo] = {}
        for item in scan.project:
            skill = _project_skill(item, issues)
            if skill is None:
                continue
            if skill.name in skills:
                logger.debug(
                    "Skipping duplicate skill %r at %s", skill.name, skill.skill_path
                )
                continue
            skills[skill.name] = skill
            self._locations[str(skill.skill_path)] = SandboxSkillLocation(
                path=str(skill.skill_path),
                base_dir=item.directory,
                files=tuple(item.files),
            )
        return skills

    async def _run(self, operation: str, request: Mapping[str, Any]) -> Any:
        return await run_helper(
            self._sandbox, operation, request, timeout=_TIMEOUT_SECONDS
        )


def _registry_skills_by_entry(skills: Iterable[SkillInfo]) -> dict[str, SkillInfo]:
    pinned: dict[str, SkillInfo] = {}
    for skill in skills:
        entry = plugin_skill_runtime_path(skill)
        if skill.registry is not None and entry is not None:
            pinned.setdefault(str(entry), skill)
    return pinned


def relocate_plugin_contexts(
    contexts: Iterable[RustPluginContextDefinition],
    skills: Mapping[str, SkillInfo],
    locate: Callable[[SkillInfo], SandboxSkillLocation | None],
) -> tuple[RustPluginContextDefinition, ...]:
    """Point plugin skills at their sandbox copies, leaving out the uncopied.

    Two skills with the same files share a copy, and Core takes one skill per
    path, so the first keeps it.
    """
    used: set[str] = set()
    relocated: list[RustPluginContextDefinition] = []
    for context in contexts:
        definitions = []
        for definition in context.capabilities.skills:
            skill = skills.get(definition.name)
            location = None if skill is None else locate(skill)
            if location is None:
                logger.warning(
                    "Dropped skill %r: it has no copy in the sandbox", definition.name
                )
                continue
            if location.path in used:
                logger.warning(
                    "Dropped skill %r: another skill has the same files",
                    definition.name,
                )
                continue
            used.add(location.path)
            definitions.append(definition.model_copy(update={"path": location.path}))
        relocated.append(
            context.model_copy(
                update={
                    "capabilities": context.capabilities.model_copy(
                        update={"skills": definitions}
                    )
                }
            )
        )
    return tuple(relocated)


def _read_trees(
    skills: Iterable[SkillInfo],
) -> tuple[list[tuple[str, _SkillTree]], list[tuple[str, Exception]]]:
    trees: dict[str, _SkillTree] = {}
    failures: list[tuple[str, Exception]] = []
    for skill in skills:
        entry = plugin_skill_runtime_path(skill)
        if entry is None or str(entry) in trees:
            continue
        try:
            trees[str(entry)] = _read_tree(entry)
        except (OSError, _SkillCopyError) as error:
            failures.append((str(entry), error))
    return list(trees.items()), failures


def _read_tree(entry: Path) -> _SkillTree:
    """A skill's ``SKILL.md`` and the regular files beside it, within limits."""
    markdown = entry.read_bytes()
    if len(markdown) > MAX_SKILL_MARKDOWN_BYTES:
        raise _SkillCopyError(f"{_SKILL_ENTRY_FILE} exceeds the size limit")
    files = {_SKILL_ENTRY_FILE: markdown}
    directory = entry.parent
    total = 0
    for current, dirnames, filenames in os.walk(directory, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in SKIPPED_SKILL_DIR_NAMES
            and not os.path.islink(os.path.join(current, name))
        )
        for name in sorted(filenames):
            path = Path(current, name)
            relative = _normalized_relative_path(path.relative_to(directory).as_posix())
            if relative == _SKILL_ENTRY_FILE or not stat.S_ISREG(path.lstat().st_mode):
                continue
            content = path.read_bytes()
            total += len(content)
            if len(files) > MAX_SKILL_ASSET_COUNT:
                raise _SkillCopyError("it has too many files")
            if len(content) > MAX_SKILL_ASSET_BYTES:
                raise _SkillCopyError(f"{relative} exceeds the size limit")
            if total > MAX_TOTAL_SKILL_ASSET_BYTES:
                raise _SkillCopyError("its files exceed the total size limit")
            files[relative] = content
    ordered = tuple(sorted(files.items(), key=lambda item: item[0].encode("utf-8")))
    return _SkillTree(digest=_materialization_digest(ordered), files=ordered)


def _normalized_relative_path(path: str) -> str:
    if not path or "\0" in path or "\\" in path or path.endswith("/"):
        raise _SkillCopyError(f"invalid file path {path!r}")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise _SkillCopyError(f"invalid file path {path!r}")
    normalized = posixpath.normpath(path)
    if normalized in {"", "."}:
        raise _SkillCopyError(f"invalid file path {path!r}")
    return normalized


def _materialization_digest(files: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files, key=lambda item: item[0].encode()):
        path_bytes = path.encode()
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _sample(paths: Iterable[str]) -> tuple[str, ...]:
    """The files the skill tool lists, as it samples them on the host."""
    return tuple(
        sorted(path for path in paths if posixpath.basename(path) != _SKILL_ENTRY_FILE)[
            :SAMPLED_SKILL_FILES
        ]
    )


def _project_skill(
    item: _ProjectSkillItem, issues: list[ConfigIssue]
) -> SkillInfo | None:
    """A project skill read in the sandbox, parsed as the host parses its own."""
    directory = item.directory
    path = posixpath.join(directory, _SKILL_ENTRY_FILE)
    if item.error is not None or item.markdown is None:
        issues.append(ConfigIssue(file=path, message=f"Failed to load: {item.error}"))
        return None
    try:
        frontmatter, body = parse_skill_markdown(item.markdown)
        metadata = SkillMetadata.model_validate(frontmatter)
    except (SkillParseError, ValidationError) as error:
        logger.warning("Failed to parse skill at %s: %s", path, error)
        issues.append(ConfigIssue(file=path, message=f"Failed to load: {error}"))
        return None
    if metadata.name != posixpath.basename(directory):
        logger.warning(
            "Skill name '%s' doesn't match directory name '%s' at %s",
            metadata.name,
            posixpath.basename(directory),
            path,
        )
    model_invocable = True
    if item.openai is not None:
        try:
            model_invocable = parse_openai_skill_metadata(
                item.openai
            ).allows_implicit_invocation
        except SkillParseError as error:
            metadata_path = posixpath.join(directory, "agents", "openai.yaml")
            logger.warning(
                "Failed to parse skill metadata at %s: %s", metadata_path, error
            )
            issues.append(
                ConfigIssue(
                    file=metadata_path, message=f"Model invocation disabled: {error}"
                )
            )
            model_invocable = False
    skill = SkillInfo.from_metadata(
        metadata,
        Path(path),
        prompt=body.strip(),
        scope=SkillScope.PROJECT,
        model_invocable=model_invocable,
    )
    # A sandbox path: never resolved against the host.
    return skill.model_copy(update={"skill_path": Path(path)})


__all__ = [
    "SKILLS_DIRNAME",
    "SandboxSessionSkills",
    "SandboxSkillLocation",
    "SandboxSkills",
    "relocate_plugin_contexts",
]
