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
    builtin_skills_dir,
    discover_session_skills,
    project_core_skills,
)
from vibe.core.skills.models import SkillInfo, SkillScope, SkillSource

if TYPE_CHECKING:
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
        RustPluginContextDefinition,
    )

pytest.importorskip("mistralai_rust_harness.protocol")


def _skill(
    name: str,
    *,
    path: Path | None = None,
    description: str = "Does a thing.",
    source: SkillSource = SkillSource.LOCAL,
) -> SkillInfo:
    return SkillInfo(
        name=name,
        description=description,
        skill_path=path,
        prompt=f"Body of {name}.",
        source=source,
        scope=SkillScope.PROJECT,
    )


def _write_skill(root: Path, name: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {name}\n", encoding="utf-8")
    return path


def _plugin_context(*skills: tuple[str, str]) -> RustPluginContextDefinition:
    """A ``toolkit`` plugin context owning ``(alias, path)`` skills."""
    from mistralai_rust_harness.protocol import (  # pyright: ignore[reportMissingImports]
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


def test_builtin_skills_are_written_to_disk_so_core_has_an_honest_path() -> None:
    """Prepare the shipped builtins, which carry no ``skill_path``.

    Do project them.

    Assert each got a real ``SKILL.md``. Core requires the path to end in
    ``SKILL.md`` and the prompt tells the model those paths are readable, so a
    fabricated one would be a lie the model can act on.
    """
    from vibe.core.skills.builtins import BUILTIN_SKILLS

    projection = project_core_skills(
        BUILTIN_SKILLS, plugin_skills={}, plugin_contexts=[]
    )

    assert {definition.name for definition in projection.definitions} == set(
        BUILTIN_SKILLS
    )
    for definition in projection.definitions:
        path = Path(definition.path)
        assert path.is_file()
        assert path.parent.parent == builtin_skills_dir()
        assert BUILTIN_SKILLS[definition.name].prompt in path.read_text(
            encoding="utf-8"
        )


def test_materializing_a_builtin_twice_leaves_the_file_alone() -> None:
    """Prepare a projection that has already run once.

    Do project again.

    Assert the file was not rewritten. Every derivation re-projects, and a
    rewrite per derivation would churn mtimes a file watcher reacts to.
    """
    from vibe.core.skills.builtins import BUILTIN_SKILLS

    first = project_core_skills(BUILTIN_SKILLS, plugin_skills={}, plugin_contexts=[])
    path = Path(first.definitions[0].path)
    stamp = path.stat().st_mtime_ns

    second = project_core_skills(BUILTIN_SKILLS, plugin_skills={}, plugin_contexts=[])

    assert Path(second.definitions[0].path) == path
    assert path.stat().st_mtime_ns == stamp


def test_a_builtin_that_cannot_be_written_is_dropped_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prepare a read-only home so materialization fails.

    Do project the builtins.

    Assert nothing raises and no definition survives. A home Vibe cannot write
    to is a degraded session, not a dead one.
    """
    from vibe.core.skills.builtins import BUILTIN_SKILLS

    def _refuse(*args: object, **kwargs: object) -> None:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "mkdir", _refuse)
    monkeypatch.setattr(Path, "write_text", _refuse)

    projection = project_core_skills(
        BUILTIN_SKILLS, plugin_skills={}, plugin_contexts=[]
    )

    assert projection.definitions == ()
    assert projection.payloads == {}


def test_materialization_does_not_change_what_a_builtin_reports_as_its_source() -> None:
    """Prepare the builtins.

    Do project them.

    Assert they still report ``BUILTIN``. The write target is deliberately a
    sibling of the skills search path: inside it, ``SkillManager`` would
    rediscover the builtins as ``LOCAL`` and the CLI would relabel them.
    """
    from vibe.core.skills.builtins import BUILTIN_SKILLS

    project_core_skills(BUILTIN_SKILLS, plugin_skills={}, plugin_contexts=[])

    for skill in BUILTIN_SKILLS.values():
        assert skill.source is SkillSource.BUILTIN
        assert skill.skill_path is None
    assert builtin_skills_dir().name not in {"skills"}


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
