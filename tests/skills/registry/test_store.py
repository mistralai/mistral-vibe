from __future__ import annotations

import base64
from pathlib import Path
import stat

import pytest

from tests.skills.registry.conftest import make_item
from vibe.core.skills.models import SkillMetadata
from vibe.core.skills.parser import parse_skill_markdown
from vibe.core.skills.registry import _store


@pytest.mark.asyncio
async def test_materialize_writes_parseable_skill() -> None:
    item = make_item(skill_id="abc", name="my_skill", description="does X", body="# Hi")
    dest = await _store.materialize(item, "my-skill")

    assert dest is not None
    assert dest == _store.skill_dir("abc", 1)
    assert await _store.is_materialized("abc", 1)
    frontmatter, body = parse_skill_markdown((dest / "SKILL.md").read_text())
    meta = SkillMetadata.model_validate(frontmatter)
    assert meta.name == "my-skill"
    assert meta.description == "does X"
    assert meta.metadata["source"] == "ai-registry"
    assert meta.metadata["skill_id"] == "abc"
    assert meta.metadata["version"] == "1"
    assert "# Hi" in body


@pytest.mark.asyncio
async def test_materialize_assets_and_exec_bit() -> None:
    encoded = base64.b64encode(b"#!/bin/sh\n").decode()
    item = make_item(
        skill_id="t",
        name="tooling",
        assets={
            "ref/notes.md": {"textContent": "n", "isExecutable": False},
            "bin/run": {"rawContent": encoded, "isExecutable": True},
        },
    )
    dest = await _store.materialize(item, "tooling")
    assert dest is not None
    assert (dest / "ref" / "notes.md").read_text() == "n"
    mode = (dest / "bin" / "run").stat().st_mode
    assert mode & stat.S_IXUSR
    assert not mode & stat.S_IXGRP
    assert not mode & stat.S_IXOTH


@pytest.mark.asyncio
async def test_materialize_empty_body_returns_none() -> None:
    assert await _store.materialize(make_item(body="   "), "x") is None


@pytest.mark.asyncio
async def test_materialize_strips_embedded_frontmatter() -> None:
    body = "---\nname: ignored\ndescription: ignored\n---\n\n# Real"
    dest = await _store.materialize(make_item(skill_id="f", body=body), "skill-one")
    assert dest is not None
    _, markdown = parse_skill_markdown((dest / "SKILL.md").read_text())
    assert "# Real" in markdown
    assert "ignored" not in markdown


@pytest.mark.asyncio
async def test_materialize_rejects_traversal_assets() -> None:
    item = make_item(
        skill_id="s",
        assets={"../escape.txt": {"textContent": "no", "isExecutable": False}},
    )
    dest = await _store.materialize(item, "safe")
    assert dest is not None
    assert not (dest.parent.parent.parent / "escape.txt").exists()


@pytest.mark.asyncio
async def test_latest_materialized_returns_highest() -> None:
    assert await _store.latest_materialized("v") is None
    await _store.materialize(make_item(skill_id="v", version=1), "v")
    await _store.materialize(make_item(skill_id="v", version=3), "v")
    assert await _store.latest_materialized("v") == 3


@pytest.mark.asyncio
async def test_export_local_strips_registry_frontmatter(tmp_path: Path) -> None:
    await _store.materialize(make_item(skill_id="e", name="exported"), "exported")
    target = tmp_path / "exported"
    await _store.export_local("e", 1, target)

    frontmatter, _ = parse_skill_markdown((target / "SKILL.md").read_text())
    assert frontmatter["name"] == "exported"
    assert "metadata" not in frontmatter


def test_skill_dir_rejects_unsafe_id() -> None:
    for bad in ("", ".", "..", "../escape", "foo/../bar", "a/b"):
        with pytest.raises(ValueError):
            _store.skill_dir(bad, 1)


@pytest.mark.asyncio
async def test_asset_cannot_overwrite_entrypoint() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="x",
            name="x",
            body="# real",
            assets={"sub/../SKILL.md": {"textContent": "PWNED", "isExecutable": False}},
        ),
        "x",
    )
    assert dest is not None
    assert "PWNED" not in (dest / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_asset_dot_path_skipped() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="d", assets={".": {"textContent": "n", "isExecutable": False}}
        ),
        "d",
    )
    assert dest is not None
    assert (dest / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_rematerialize_drops_stale_assets() -> None:
    dest = await _store.materialize(
        make_item(
            skill_id="r",
            assets={
                "keep.md": {"textContent": "k", "isExecutable": False},
                "old.md": {"textContent": "o", "isExecutable": False},
            },
        ),
        "r",
    )
    assert dest is not None and (dest / "old.md").exists()

    await _store.materialize(
        make_item(
            skill_id="r",
            assets={"keep.md": {"textContent": "k", "isExecutable": False}},
        ),
        "r",
    )
    assert (dest / "keep.md").exists()
    assert not (dest / "old.md").exists()


@pytest.mark.asyncio
async def test_empty_body_drops_existing_cache() -> None:
    await _store.materialize(make_item(skill_id="c", version=1), "c")
    assert await _store.is_materialized("c", 1)

    assert (
        await _store.materialize(make_item(skill_id="c", version=1, body="  "), "c")
        is None
    )
    assert not await _store.is_materialized("c", 1)


@pytest.mark.asyncio
async def test_materialize_failure_preserves_previous_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store.materialize(make_item(skill_id="p", version=1, body="# good"), "p")
    assert await _store.is_materialized("p", 1)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_store, "_write_assets", boom)
    with pytest.raises(OSError):
        await _store.materialize(make_item(skill_id="p", version=1, body="# new"), "p")

    # Prior cache survives the failed re-materialize.
    assert await _store.is_materialized("p", 1)
    _, md = parse_skill_markdown((_store.skill_dir("p", 1) / "SKILL.md").read_text())
    assert "# good" in md


@pytest.mark.asyncio
async def test_prune_keeps_active_drops_others() -> None:
    await _store.materialize(make_item(skill_id="a", version=1), "a")
    await _store.materialize(make_item(skill_id="a", version=2), "a")
    await _store.materialize(make_item(skill_id="b", version=1), "b")

    await _store.prune({("a", 2)})

    assert await _store.is_materialized("a", 2)
    assert not await _store.is_materialized("a", 1)
    assert not await _store.is_materialized("b", 1)
