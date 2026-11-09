"""Projection of the workspace skill catalogue into Unified Harness config.

Core validates its capability set atomically: one duplicate name or path, or one
description Core will not accept, rejects the entire configuration and the
session never starts. So the interesting cases here are all about what the
projection refuses to hand over.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from vibe.app_server._skills import (
    SkillProjection,
    discover_session_skills,
    project_core_skills,
)
from vibe.core.skills.models import SkillInfo, SkillScope, SkillSource

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.protocol import RustPluginContextDefinition

pytest.importorskip("mistralai_vibe_local_harness.protocol")


def _skill(
    name: str,
    *,
    path: Path | None = None,
    description: str = "Does a thing.",
    source: SkillSource = SkillSource.LOCAL,
    model_invocable: bool = True,
) -> SkillInfo:
    return SkillInfo(
        name=name,
        description=description,
        skill_path=path,
        prompt=f"Body of {name}.",
        source=source,
        scope=SkillScope.PROJECT,
        model_invocable=model_invocable,
    )


def _write_skill(root: Path, name: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n", encoding="utf-8")
    return path


def _plugin_context(*skills: tuple[str, str]) -> RustPluginContextDefinition:
    """A ``toolkit`` plugin context owning ``(alias, path)`` skills."""
    from mistralai_vibe_local_harness.protocol import (
        RustHarnessCapabilitySet,
        RustPluginContextDefinition,
        RustSkillDefinition,
    )

    return RustPluginContextDefinition(
        name="toolkit",
        description="A plugin.",
        path="/plugins/toolkit",
        capabilities=RustHarnessCapabilitySet(
            skills=[
                RustSkillDefinition(name=alias, description="Does a thing.", path=path)
                for alias, path in skills
            ]
        ),
    )


def test_every_advertised_skill_has_a_body_the_runtime_can_serve(
    tmp_path: Path,
) -> None:
    """Prepare a root skill and a plugin skill.

    Do project them together.

    Assert every name Core can put in the ``skill`` tool enum is a key in the
    payload map. A name in the enum with no payload is a tool call that can only
    fail, so the two have to be built in one pass.
    """
    root_path = _write_skill(tmp_path / "root", "review")
    plugin_path = _write_skill(tmp_path / "plugin", "deploy")
    plugin_skill = _skill("deploy", path=plugin_path)

    projection = project_core_skills(
        {"review": _skill("review", path=root_path)},
        plugin_skills={"toolkit:deploy": plugin_skill},
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(plugin_path)))],
    )

    assert [definition.name for definition in projection.definitions] == ["review"]
    # The plugin definition is *not* re-emitted at the root; it reaches Core
    # through its own plugin context. Its payload still has to be here.
    assert set(projection.payloads) == {"review", "toolkit:deploy"}
    assert "Body of review." in projection.payloads["review"]
    assert "Body of deploy." in projection.payloads["toolkit:deploy"]


def test_explicit_only_skills_stay_in_catalogue_but_not_model_capabilities(
    tmp_path: Path,
) -> None:
    automatic_path = _write_skill(tmp_path / "root", "automatic")
    root_explicit_path = _write_skill(tmp_path / "root", "root-explicit")
    plugin_explicit_path = _write_skill(tmp_path / "plugin", "plugin-explicit")
    plugin_alias = "toolkit:plugin-explicit"

    projection = project_core_skills(
        {
            "automatic": _skill("automatic", path=automatic_path),
            "root-explicit": _skill(
                "root-explicit", path=root_explicit_path, model_invocable=False
            ),
        },
        plugin_skills={
            plugin_alias: _skill(
                plugin_alias,
                path=plugin_explicit_path,
                source=SkillSource.PLUGIN,
                model_invocable=False,
            )
        },
        plugin_contexts=[_plugin_context((plugin_alias, str(plugin_explicit_path)))],
    )

    assert [definition.name for definition in projection.definitions] == ["automatic"]
    assert projection.plugin_contexts[0].capabilities.skills == []
    assert set(projection.model_payloads) == {"automatic"}
    assert set(projection.payloads) == {"automatic", "root-explicit", plugin_alias}
    assert {skill.name for skill in projection.catalogue} == set(projection.payloads)


def test_a_plugin_skill_is_offered_to_the_client_under_its_alias(
    tmp_path: Path,
) -> None:
    """Prepare a root skill and a plugin skill.

    Do project them together.

    Assert the client catalogue carries both, the plugin one under the alias
    Core was configured with. Plugin skills reach Core through their own
    context rather than the root definitions, and a catalogue built from the
    root set alone is how they end up loadable by the model but missing from
    the ``/`` menu.
    """
    root_path = _write_skill(tmp_path / "root", "review")
    plugin_path = _write_skill(tmp_path / "plugin", "deploy")

    projection = project_core_skills(
        {"review": _skill("review", path=root_path)},
        plugin_skills={
            "toolkit:deploy": _skill(
                "toolkit:deploy", path=plugin_path, source=SkillSource.PLUGIN
            )
        },
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(plugin_path)))],
    )

    assert {skill.name for skill in projection.catalogue} == {
        "review",
        "toolkit:deploy",
    }
    assert {skill.name for skill in projection.catalogue} == set(projection.payloads)
    plugin_entry = next(
        skill for skill in projection.catalogue if skill.name == "toolkit:deploy"
    )
    assert plugin_entry.source is SkillSource.PLUGIN
    assert plugin_entry.user_invocable is True


def test_a_plugin_skill_is_catalogued_under_the_name_its_payload_is_keyed_by(
    tmp_path: Path,
) -> None:
    """Prepare a resolved plugin skill still carrying its unqualified name.

    Do project it.

    Assert the catalogue reports the alias anyway. ``/name`` looks the body up
    in the payload map, which is keyed by the alias Core was configured with,
    so cataloguing anything else offers a command that resolves to nothing.
    """
    plugin_path = _write_skill(tmp_path / "plugin", "deploy")

    projection = project_core_skills(
        {},
        plugin_skills={"toolkit:deploy": _skill("deploy", path=plugin_path)},
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(plugin_path)))],
    )

    assert [skill.name for skill in projection.catalogue] == ["toolkit:deploy"]


def test_a_skill_core_rejected_is_not_offered_to_the_client(tmp_path: Path) -> None:
    """Prepare a root skill with a name Core's Agent Skill grammar rejects.

    Do project it.

    Assert it is absent from the client catalogue. It has no payload either, so
    listing it would put a name in the ``/`` menu that injects nothing and that
    the model cannot load.
    """
    projection = project_core_skills(
        {
            "Not A Skill Name": _skill(
                "Not A Skill Name", path=_write_skill(tmp_path / "root", "bad")
            ),
            "review": _skill("review", path=_write_skill(tmp_path / "root", "review")),
        },
        plugin_skills={},
        plugin_contexts=[],
    )

    assert [skill.name for skill in projection.catalogue] == ["review"]


def test_a_root_skill_a_plugin_shadows_is_offered_once_under_the_alias(
    tmp_path: Path,
) -> None:
    """Prepare one ``SKILL.md`` reachable both as a root skill and via a plugin.

    Do project it.

    Assert the client is offered the plugin alias and not the root name. The
    root copy is dropped before it gets a payload, so offering its name would
    be a slash command that injects nothing.
    """
    shared = _write_skill(tmp_path / "plugin", "deploy")

    projection = project_core_skills(
        {"deploy": _skill("deploy", path=shared)},
        plugin_skills={
            "toolkit:deploy": _skill(
                "toolkit:deploy", path=shared, source=SkillSource.PLUGIN
            )
        },
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(shared)))],
    )

    assert [skill.name for skill in projection.catalogue] == ["toolkit:deploy"]


def test_a_root_skill_a_plugin_already_owns_is_dropped_rather_than_duplicated(
    tmp_path: Path,
) -> None:
    """Prepare one ``SKILL.md`` reachable both as a root skill and via a plugin.

    Do project it.

    Assert it appears once. Core rejects a duplicate *path* across root and
    plugins for the whole config, so a naive union would take the session down
    instead of showing a skill twice.
    """
    shared = _write_skill(tmp_path / "plugin", "deploy")
    plugin_skill = _skill("deploy", path=shared)

    projection = project_core_skills(
        {"deploy": _skill("deploy", path=shared)},
        plugin_skills={"toolkit:deploy": plugin_skill},
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(shared)))],
    )

    assert projection.definitions == ()
    assert set(projection.payloads) == {"toolkit:deploy"}


def test_a_symlinked_duplicate_is_still_the_same_skill(tmp_path: Path) -> None:
    """Prepare a plugin skill and a root skill reaching it through a symlink.

    Do project them.

    Assert the root copy is dropped. Core compares raw path strings, so this
    would not be a duplicate to Core -- but two entries for one file is still a
    catalogue bug, and resolving is strictly safer than Core's own check.
    """
    real = _write_skill(tmp_path / "plugin", "deploy")
    link = tmp_path / "link"
    link.symlink_to(real.parent, target_is_directory=True)
    plugin_skill = _skill("deploy", path=real)

    projection = project_core_skills(
        {"deploy": _skill("deploy", path=link / "SKILL.md")},
        plugin_skills={"toolkit:deploy": plugin_skill},
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(real)))],
    )

    assert projection.definitions == ()


def test_a_skill_core_would_reject_is_dropped_without_taking_the_others_down() -> None:
    """Prepare a skill with a name Core's Agent Skill grammar rejects.

    Do project it next to a valid one.

    Assert only the valid one survives and nothing raises. Validation is atomic
    on Core's side, so one malformed skill would otherwise cost the session.
    """
    projection = project_core_skills(
        {
            "Not A Skill Name": _skill(
                "Not A Skill Name", path=Path("/skills/bad/SKILL.md")
            ),
            "review": _skill("review", path=Path("/skills/review/SKILL.md")),
        },
        plugin_skills={},
        plugin_contexts=[],
    )

    assert [definition.name for definition in projection.definitions] == ["review"]
    assert set(projection.payloads) == {"review"}


def test_a_root_skill_without_a_path_is_dropped_not_raised() -> None:
    """Prepare a pathless root skill.

    Do project it.

    Assert nothing raises and no definition survives. A skill the session
    cannot point at on disk is a degraded session, not a dead one.
    """
    projection = project_core_skills(
        {"ghost": _skill("ghost")}, plugin_skills={}, plugin_contexts=[]
    )

    assert projection.definitions == ()
    assert projection.payloads == {}


def test_a_plugin_definition_without_a_resolved_skill_is_left_without_a_payload(
    tmp_path: Path,
) -> None:
    """Prepare a plugin definition whose skill the resolver did not surface.

    Do project it.

    Assert its path is still claimed. The payload is missing -- the tool call
    fails recoverably -- but the claim is what stops a root skill at the same
    path from turning that into an atomic config rejection.
    """
    shared = _write_skill(tmp_path / "plugin", "deploy")

    projection = project_core_skills(
        {"deploy": _skill("deploy", path=shared)},
        plugin_skills={},
        plugin_contexts=[_plugin_context(("toolkit:deploy", str(shared)))],
    )

    assert projection.definitions == ()
    assert projection.payloads == {}
    assert projection.catalogue == ()


def test_denying_the_skill_tool_denies_plugin_skills_too(tmp_path: Path) -> None:
    """Prepare a plugin context carrying one skill.

    Do discover the catalogue once with the ``skill`` tool available and once
    without.

    Assert the denied pass hands Core a stripped context. Plugin skills reach
    Core through ``config.plugins`` rather than the root capability set, so
    withholding only the root definitions would leave the model looking at a
    catalogue the Runtime is configured to refuse.
    """
    from vibe.core.config import VibeConfigSchema
    from vibe.core.config.harness_files import HarnessFilesManager

    plugin_path = _write_skill(tmp_path / "plugin", "deploy")
    contexts = [_plugin_context(("toolkit:deploy", str(plugin_path)))]

    def discover(*, skill_tool_available: bool) -> SkillProjection:
        _, projection = discover_session_skills(
            VibeConfigSchema,
            harness_files=HarnessFilesManager(sources=(), cwd=tmp_path),
            plugin_skills={"toolkit:deploy": _skill("deploy", path=plugin_path)},
            plugin_contexts=contexts,
            skill_tool_available=skill_tool_available,
        )
        return projection

    allowed = discover(skill_tool_available=True)
    denied = discover(skill_tool_available=False)

    assert [
        skill.name
        for context in allowed.plugin_contexts
        for skill in context.capabilities.skills
    ] == ["toolkit:deploy"]
    assert denied.definitions == ()
    assert all(not context.capabilities.skills for context in denied.plugin_contexts)
    assert "toolkit:deploy" in denied.payloads
    # Withheld from Core, not from the user: `/toolkit:deploy` injects the body
    # out of the payload map without Core ever loading the skill itself.
    assert "toolkit:deploy" in {skill.name for skill in denied.catalogue}
