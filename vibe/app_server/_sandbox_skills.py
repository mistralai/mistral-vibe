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
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
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
from vibe.core.skills.models import SkillInfo, SkillMetadata, SkillScope
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


@dataclass(frozen=True, slots=True)
class SandboxSessionSkills:
    """What one sandboxed session takes from its sandbox's skills."""

    project_skills: Mapping[str, SkillInfo]
    """The open roots' own skills, by name, at their sandbox paths."""
    issues: tuple[ConfigIssue, ...]
    """The project skills that failed to load, and the host skills left out."""
    locate: Callable[[SkillInfo], SandboxSkillLocation | None]


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

    async def prepare_session(
        self, skills: Iterable[SkillInfo], *, roots: Sequence[Path]
    ) -> SandboxSessionSkills:
        """Copy ``skills`` into the sandbox and read the project skills of ``roots``.

        A skill that cannot be copied is left out, as is every skill when the
        sandbox cannot run the copy, and each one left out is a config issue.
        Raises :class:`SandboxUnavailableError` when the sandbox itself fails.
        """
        trees, issues = await asyncio.to_thread(_read_trees, skills)
        scan = await self._scan(trees, roots=roots, issues=issues)
        if scan is None:
            return SandboxSessionSkills(
                project_skills={}, issues=tuple(issues), locate=self.locate
            )
        await self._upload(trees, issues)
        project = self._project_skills(scan, issues)
        return SandboxSessionSkills(
            project_skills=project, issues=tuple(issues), locate=self.locate
        )

    async def install(self, skills: Iterable[SkillInfo]) -> None:
        """Copy ``skills`` into the sandbox, unless they are already there.

        A skill left out is only logged: this runs outside a session's setup,
        with no config issues to report it in.
        """
        trees, issues = await asyncio.to_thread(_read_trees, skills)
        if self._root is None or any(
            tree.digest not in self._present for _, tree in trees
        ):
            if await self._scan(trees, roots=(), issues=issues) is None:
                return
        await self._upload(trees, issues)

    def locate(self, skill: SkillInfo) -> SandboxSkillLocation | None:
        """Where ``skill`` is in the sandbox, or None when it was not copied."""
        entry = plugin_skill_runtime_path(skill)
        return None if entry is None else self._locations.get(str(entry))

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
            logger.warning(
                "Skills are off for this sandboxed session: the sandbox could "
                "not list them (%s)",
                error,
            )
            issues.extend(
                _left_out(entry, f"the sandbox could not list skills: {error}")
                for entry, _ in trees
            )
            return None
        self._root = scan.root
        self._present.update(scan.present)
        return scan

    async def _upload(
        self, trees: Sequence[tuple[str, _SkillTree]], issues: list[ConfigIssue]
    ) -> None:
        assert self._root is not None
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
                    _left_out(entry, f"it could not be copied: {failures[tree.digest]}")
                )
                continue
            base_dir = posixpath.join(self._root, tree.digest)
            self._locations[entry] = SandboxSkillLocation(
                path=posixpath.join(base_dir, _SKILL_ENTRY_FILE),
                base_dir=base_dir,
                files=_sample(path for path, _ in tree.files),
            )

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


def _left_out(entry: str, reason: str) -> ConfigIssue:
    """Log and report a host skill the sandboxed session goes without."""
    logger.warning("Dropped skill %s: %s", entry, reason)
    return ConfigIssue(file=entry, message=f"Skill left out of the sandbox: {reason}")


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
) -> tuple[list[tuple[str, _SkillTree]], list[ConfigIssue]]:
    trees: dict[str, _SkillTree] = {}
    issues: list[ConfigIssue] = []
    for skill in skills:
        entry = plugin_skill_runtime_path(skill)
        if entry is None or str(entry) in trees:
            continue
        try:
            trees[str(entry)] = _read_tree(entry)
        except (OSError, _SkillCopyError) as error:
            issues.append(_left_out(str(entry), f"it cannot be copied: {error}"))
    return list(trees.items()), issues


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
