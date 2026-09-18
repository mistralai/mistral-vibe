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
from dataclasses import dataclass
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vibe.app_server.models import ConfigIssue
from vibe.core.paths import VIBE_HOME
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import DISABLE_MODEL_INVOCATION_FIELD, SkillInfo
from vibe.core.tools.builtins.skill import render_skill_result, sample_skill_files

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import (
        RustPluginContextDefinition,
        RustSkillDefinition,
    )

    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager

logger = logging.getLogger(__name__)


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


def discover_session_skills(
    config: Callable[[], VibeConfigSchema],
    *,
    harness_files: HarnessFilesManager,
    plugin_skills: Mapping[str, SkillInfo],
    plugin_contexts: Iterable[RustPluginContextDefinition],
    skill_tool_available: bool,
) -> tuple[list[ConfigIssue], SkillProjection]:
    # The Python builtins reach a unified session as skills of the shipped
    # `vibe` plugin, so loading them here too would offer each one twice under
    # two names.
    manager = SkillManager(config, harness_files=harness_files, include_builtins=False)
    issues = [
        ConfigIssue(file=str(issue.file), message=issue.message)
        for issue in sorted(
            manager.config_issues, key=lambda item: (str(item.file), item.message)
        )
    ]
    contexts = tuple(plugin_contexts)
    projection = project_core_skills(
        manager.available_skills, plugin_skills=plugin_skills, plugin_contexts=contexts
    )
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
        ),
    )


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


def _payload(skill: SkillInfo) -> str:
    """Render the body the Runtime serves, file sample included."""
    return render_skill_result(skill, sample_skill_files(skill.skill_dir)).content


def project_core_skills(
    root_skills: Mapping[str, SkillInfo],
    *,
    plugin_skills: Mapping[str, SkillInfo],
    plugin_contexts: Iterable[RustPluginContextDefinition],
) -> SkillProjection:
    claimed: set[Path] = set()
    payloads: dict[str, str] = {}
    model_payloads: dict[str, str] = {}
    definitions: list[RustSkillDefinition] = []
    catalogue: list[SkillInfo] = []

    contexts = project_model_invocable_plugin_contexts(plugin_contexts, plugin_skills)
    for definition in (
        definition for context in contexts for definition in context.capabilities.skills
    ):
        claimed.add(_resolved(Path(definition.path)))
        skill = plugin_skills.get(definition.name)
        if skill is not None:
            payload = _payload(skill)
            payloads[definition.name] = payload
            model_payloads[definition.name] = payload
            catalogue.append(skill.model_copy(update={"name": definition.name}))

    for name, skill in plugin_skills.items():
        if skill.model_invocable or name in payloads:
            continue
        if skill.skill_path is not None:
            claimed.add(_resolved(skill.skill_path))
        payloads[name] = _payload(skill)
        catalogue.append(skill.model_copy(update={"name": name}))

    for name, skill in root_skills.items():
        path = skill.skill_path
        if path is None:
            path = _materialize_builtin_skill(skill)
        if path is None:
            continue
        resolved = _resolved(path)
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
        payload = _payload(skill)
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
    )


def builtin_skills_dir() -> Path:
    return VIBE_HOME.path / "builtin-skills"


def _materialize_builtin_skill(skill: SkillInfo) -> Path | None:
    path = builtin_skills_dir() / skill.name / "SKILL.md"
    content = _render_builtin_skill(skill)
    try:
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as error:
        logger.warning(
            "Dropped builtin skill %r: failed to materialize %s: %s",
            skill.name,
            path,
            error,
        )
        return None
    return path


def _render_builtin_skill(skill: SkillInfo) -> str:
    lines = [
        "---",
        f"name: {json.dumps(skill.name)}",
        f"description: {json.dumps(skill.description)}",
    ]
    if skill.allowed_tools:
        lines.append("allowed-tools: " + json.dumps(list(skill.allowed_tools)))
    if not skill.user_invocable:
        lines.append("user-invocable: false")
    if not skill.model_invocable:
        lines.append(f"{DISABLE_MODEL_INVOCATION_FIELD}: true")
    lines.extend(["---", "", skill.prompt, ""])
    return "\n".join(lines)


def _accept_skill(
    name: str, *, description: str, path: Path
) -> RustSkillDefinition | None:
    from mistralai_vibe_local_harness.protocol import RustSkillDefinition

    try:
        return RustSkillDefinition(name=name, description=description, path=str(path))
    except ValidationError as error:
        logger.warning("Dropped skill %r at %s: %s", name, path, error, exc_info=True)
        return None


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


__all__ = [
    "SkillProjection",
    "builtin_skills_dir",
    "discover_session_skills",
    "project_core_skills",
    "project_model_invocable_plugin_contexts",
]
