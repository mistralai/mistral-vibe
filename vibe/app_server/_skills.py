"""Projection of the workspace skill catalogue into Unified Harness configuration.

Core is handed *what a skill is* — name, description, and a real ``SKILL.md``
path it renders into the prompt. The local Runtime is handed *what a skill
says* — the already-rendered ``<skill_content>`` block. The client is handed
*what a skill is called* — the list behind the ``/`` menu. All three come out
of one pass over the same sources, which is what keeps the prompt catalogue,
the Runtime payload map, and the slash menu from drifting.

The Runtime never parses frontmatter and never learns what a skill is; that
stays on this side of the seam, exactly as it does for plugins.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vibe.app_server.models import ConfigIssue
from vibe.core.skills.manager import ShadowedSkill, SkillManager
from vibe.core.skills.models import InstalledSkill, SkillInfo
from vibe.core.skills.registry import mark_versions_used
from vibe.core.tools.builtins.skill import render_skill_result, sample_skill_files

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import (
        RustPluginContextDefinition,
        RustSkillDefinition,
    )
    from vibe.app_server._sandbox_skills import (
        SandboxSessionSkills,
        SandboxSkillLocation,
    )
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager

logger = logging.getLogger(__name__)

type RegistryVersions = Mapping[str, tuple[str, int]]


@dataclass(frozen=True, slots=True)
class SkillProjection:
    """One derivation's view of the skill catalogue.

    ``definitions`` are the root skills Core is configured with; plugin skills
    reach Core through ``plugin_contexts``. ``payloads`` covers every projected
    skill, while ``model_payloads`` contains only the subset Core may
    expose through the ``skill`` tool.

    ``catalogue`` is the same set spelled for the client: one entry per name
    that survived projection, plugin aliases included. It is what the runtime
    snapshot lists, so ``/skill-name`` offers exactly the names that have a
    body — listing a root skill Core dropped, or hiding a plugin skill Core
    accepted, are the two ways this drifts.
    """

    definitions: tuple[RustSkillDefinition, ...]
    payloads: Mapping[str, str]
    model_payloads: Mapping[str, str]
    plugin_contexts: tuple[RustPluginContextDefinition, ...]
    catalogue: tuple[SkillInfo, ...]
    registry_versions: RegistryVersions
    shadowed: tuple[ShadowedSkill, ...] = ()


def resolve_session_skills(
    config: Callable[[], VibeConfigSchema],
    *,
    harness_files: HarnessFilesManager,
    sandbox: SandboxSessionSkills | None = None,
) -> SkillManager:
    """With ``sandbox``, the project's own skills are those read in the sandbox, ranked
    where the host ranks its project's files.
    """
    return SkillManager(
        config,
        harness_files=harness_files,
        project_skills=None if sandbox is None else sandbox.project_skills,
    )


def host_skills(
    config: Callable[[], VibeConfigSchema], *, harness_files: HarnessFilesManager
) -> Mapping[str, SkillInfo]:
    return resolve_session_skills(config, harness_files=harness_files).available_skills


def installed_session_skills(
    config: Callable[[], VibeConfigSchema],
    *,
    harness_files: HarnessFilesManager,
    sandbox: SandboxSessionSkills | None = None,
) -> list[InstalledSkill]:
    """In a sandbox, an active one is listed in the version the session runs."""
    installed = resolve_session_skills(
        config, harness_files=harness_files, sandbox=sandbox
    ).installed_skills()
    return installed if sandbox is None else sandbox.installed(installed)


def registry_versions_of(skills: Iterable[SkillInfo]) -> RegistryVersions:
    return {
        skill.name: (skill.registry.skill_id, skill.registry.version)
        for skill in skills
        if skill.registry is not None
    }


def resolved_registry_versions(
    config: Callable[[], VibeConfigSchema],
    *,
    harness_files: HarnessFilesManager,
    sandbox: SandboxSessionSkills | None = None,
) -> RegistryVersions:
    """As derivations resolve them, before a sandbox keeps an older copy for a version
    it holds no copy of.
    """
    manager = resolve_session_skills(
        config, harness_files=harness_files, sandbox=sandbox
    )
    return registry_versions_of(manager.available_skills.values())


def discover_session_skills(
    config: Callable[[], VibeConfigSchema],
    *,
    harness_files: HarnessFilesManager,
    plugin_skills: Mapping[str, SkillInfo],
    plugin_contexts: Iterable[RustPluginContextDefinition],
    skill_tool_available: bool,
    sandbox: SandboxSessionSkills | None = None,
) -> tuple[list[ConfigIssue], SkillProjection]:
    """Project the session's skills.

    With ``sandbox``, the model reads skills in the sandbox: each one is
    pointed at its copy there, the project's own skills are those read from
    the sandbox and rank as the host ranks its own, and a registry skill that
    tracks an alias and whose version has no copy keeps an older version the
    sandbox holds.

    The registry versions the session loads are marked used as they are read,
    so a cleanup keeps them until the session's lease holds them. One that a
    cleanup took as it was read makes a session on the host resolve again,
    so it never runs a version that is gone; a sandboxed session runs the
    copies in its sandbox.
    """
    manager, root_skills = _root_skills(config, harness_files, sandbox)
    marked = mark_versions_used(registry_versions_of(root_skills.values()).values())
    if not marked and sandbox is None:
        manager, root_skills = _root_skills(config, harness_files, sandbox)
        mark_versions_used(registry_versions_of(root_skills.values()).values())
    issues = [
        ConfigIssue(file=str(issue.file), message=issue.message)
        for issue in sorted(
            manager.config_issues, key=lambda item: (str(item.file), item.message)
        )
    ]
    if sandbox is not None:
        issues.extend(sandbox.issues)
    contexts = tuple(plugin_contexts)
    projection = project_core_skills(
        root_skills,
        plugin_skills=plugin_skills,
        plugin_contexts=contexts,
        locate=None if sandbox is None else sandbox.locate,
    )
    projection = replace(projection, shadowed=manager.shadowed)
    if skill_tool_available:
        return issues, projection
    return (
        issues,
        SkillProjection(
            definitions=(),
            payloads=projection.payloads,
            model_payloads={},
            plugin_contexts=tuple(_without_skills(context) for context in contexts),
            catalogue=projection.catalogue,
            registry_versions=projection.registry_versions,
            shadowed=projection.shadowed,
        ),
    )


def _root_skills(
    config: Callable[[], VibeConfigSchema],
    harness_files: HarnessFilesManager,
    sandbox: SandboxSessionSkills | None,
) -> tuple[SkillManager, Mapping[str, SkillInfo]]:
    """In a sandbox, an alias pin whose version has no copy loads the older version kept
    for it.
    """
    manager = resolve_session_skills(
        config, harness_files=harness_files, sandbox=sandbox
    )
    if sandbox is None:
        return manager, manager.available_skills
    return manager, {
        name: sandbox.copied_version(skill)
        for name, skill in manager.available_skills.items()
    }


def _without_skills(
    context: RustPluginContextDefinition,
) -> RustPluginContextDefinition:
    if not context.capabilities.skills:
        return context
    return context.model_copy(
        update={"capabilities": context.capabilities.model_copy(update={"skills": []})}
    )


def project_model_invocable_plugin_contexts(
    contexts: Iterable[RustPluginContextDefinition], skills: Mapping[str, SkillInfo]
) -> tuple[RustPluginContextDefinition, ...]:
    """Remove explicit-only skills from model-visible plugin contexts."""
    projected: list[RustPluginContextDefinition] = []
    for context in contexts:
        definitions = [
            definition
            for definition in context.capabilities.skills
            if (skill := skills.get(definition.name)) is None or skill.model_invocable
        ]
        if len(definitions) == len(context.capabilities.skills):
            projected.append(context)
            continue
        projected.append(
            context.model_copy(
                update={
                    "capabilities": context.capabilities.model_copy(
                        update={"skills": definitions}
                    )
                }
            )
        )
    return tuple(projected)


def _payload(skill: SkillInfo, location: SandboxSkillLocation | None = None) -> str:
    """Render the body the Runtime serves, file sample included."""
    if location is not None:
        return render_skill_result(
            skill, list(location.files), base_dir=location.base_dir
        ).content
    return render_skill_result(skill, sample_skill_files(skill.skill_dir)).content


def project_core_skills(  # noqa: PLR0912, PLR0915 - host and sandbox halves of one pass
    root_skills: Mapping[str, SkillInfo],
    *,
    plugin_skills: Mapping[str, SkillInfo],
    plugin_contexts: Iterable[RustPluginContextDefinition],
    locate: Callable[[SkillInfo], SandboxSkillLocation | None] | None = None,
) -> SkillProjection:
    """Project root and plugin skills into Core definitions and payloads.

    With ``locate``, every skill is pointed at its copy in the sandbox, and a
    skill without one is left out.
    """
    claimed: set[str] = set()
    payloads: dict[str, str] = {}
    model_payloads: dict[str, str] = {}
    definitions: list[RustSkillDefinition] = []
    catalogue: list[SkillInfo] = []

    contexts = project_model_invocable_plugin_contexts(plugin_contexts, plugin_skills)
    if locate is not None:
        from vibe.app_server._sandbox_skills import relocate_plugin_contexts

        contexts = relocate_plugin_contexts(contexts, plugin_skills, locate)
    for definition in (
        definition for context in contexts for definition in context.capabilities.skills
    ):
        claimed.add(
            definition.path
            if locate is not None
            else str(_resolved(Path(definition.path)))
        )
        skill = plugin_skills.get(definition.name)
        if skill is not None:
            payload = _payload(skill, None if locate is None else locate(skill))
            payloads[definition.name] = payload
            model_payloads[definition.name] = payload
            catalogue.append(skill.model_copy(update={"name": definition.name}))

    for name, skill in plugin_skills.items():
        if skill.model_invocable or name in payloads:
            continue
        location = None
        if locate is not None:
            location = locate(skill)
            if location is None:
                logger.warning("Dropped skill %r: it has no copy in the sandbox", name)
                continue
            claimed.add(location.path)
        elif skill.skill_path is not None:
            claimed.add(str(_resolved(skill.skill_path)))
        payloads[name] = _payload(skill, location)
        catalogue.append(skill.model_copy(update={"name": name}))

    for name, skill in root_skills.items():
        location = None
        if locate is not None:
            location = locate(skill)
            if location is None:
                logger.warning("Dropped skill %r: it has no copy in the sandbox", name)
                continue
            path: str = location.path
            resolved = location.path
        else:
            if skill.skill_path is None:
                logger.warning("Dropped root skill %r: it has no path", name)
                continue
            path = str(skill.skill_path)
            resolved = str(_resolved(skill.skill_path))
        if resolved in claimed:
            logger.debug(
                "Skipping root skill %r at %s: the path is already configured by a plugin",
                name,
                resolved,
            )
            continue
        definition = _accept_skill(name, description=skill.description, path=path)
        if definition is None:
            continue
        claimed.add(resolved)
        payload = _payload(skill, location)
        payloads[name] = payload
        if skill.model_invocable:
            definitions.append(definition)
            model_payloads[name] = payload
        catalogue.append(skill)

    return SkillProjection(
        definitions=tuple(definitions),
        payloads=payloads,
        model_payloads=model_payloads,
        plugin_contexts=contexts,
        catalogue=tuple(catalogue),
        registry_versions=registry_versions_of(root_skills.values()),
    )


def _accept_skill(
    name: str, *, description: str, path: str
) -> RustSkillDefinition | None:
    from mistralai_vibe_local_harness.protocol import RustSkillDefinition

    try:
        return RustSkillDefinition(name=name, description=description, path=path)
    except ValidationError as error:
        logger.warning("Dropped skill %r at %s: %s", name, path, error, exc_info=True)
        return None


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


__all__ = [
    "RegistryVersions",
    "SkillProjection",
    "discover_session_skills",
    "host_skills",
    "installed_session_skills",
    "project_core_skills",
    "project_model_invocable_plugin_contexts",
    "registry_versions_of",
    "resolve_session_skills",
    "resolved_registry_versions",
]
