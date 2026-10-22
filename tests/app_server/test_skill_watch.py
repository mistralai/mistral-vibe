from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vibe.app_server._skill_watch import (
    is_skill_change,
    skill_watch_roots,
    watch_skill_changes,
)
from vibe.core.config.harness_files import HarnessFilesManager


def _trusted(root: Path) -> HarnessFilesManager:
    files = HarnessFilesManager(sources=("project",)).for_session(root)
    files.trust_store.trust_for_session(root)
    return files


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/repo/.vibe/skills/review/SKILL.md", True),
        ("/repo/.vibe/skills.toml", True),
        ("/repo/.vibe/skills/review", True),
        ("/repo/.agents", True),
        ("/repo/.vibe/skills/review/notes.md", False),
        ("/repo/src/main.py", False),
    ],
)
def test_only_changes_discovery_reads_count(path: str, expected: bool) -> None:
    assert is_skill_change(path, [Path("/repo/.vibe/skills")]) is expected


def test_watches_existing_skill_dirs_and_the_parents_that_could_grow_one(
    tmp_path: Path,
) -> None:
    (tmp_path / ".vibe" / "skills").mkdir(parents=True)

    recursive, flat, _awaited = skill_watch_roots(_trusted(tmp_path))

    assert recursive == [(tmp_path / ".vibe" / "skills").resolve()]
    assert tmp_path.resolve() in flat
    assert (tmp_path / ".vibe").resolve() in flat


def test_watches_the_parent_of_a_configured_skill_path_not_created_yet(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "team" / "skills"
    configured.parent.mkdir()

    roots = skill_watch_roots(HarnessFilesManager(sources=()), configured=[configured])

    assert roots.recursive == []
    assert roots.flat == [configured.parent.resolve()]
    assert roots.awaited == [configured.resolve()]
    assert is_skill_change(str(configured.resolve()), [], roots.awaited)


def test_an_untrusted_project_is_not_watched(tmp_path: Path) -> None:
    (tmp_path / ".vibe" / "skills").mkdir(parents=True)

    recursive, flat, _awaited = skill_watch_roots(
        HarnessFilesManager(sources=("project",)).for_session(tmp_path)
    )

    assert recursive == []
    assert flat == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_new_skill_file_yields_one_change(tmp_path: Path) -> None:
    """Prepare a watched skills directory.

    Do write a skill into it.

    Assert the watch reports one debounced change, and nothing for an
    unrelated file written next to it.
    """
    skills = tmp_path / ".vibe" / "skills"
    skills.mkdir(parents=True)
    files = _trusted(tmp_path)
    changes = watch_skill_changes(lambda: skill_watch_roots(files), debounce_ms=50)
    first = asyncio.ensure_future(anext(changes))
    await asyncio.sleep(0.5)

    (tmp_path / "README.md").write_text("unrelated", encoding="utf-8")
    skill = skills / "review" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: review\ndescription: x\n---\n", encoding="utf-8")

    await asyncio.wait_for(first, timeout=10)
    await changes.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_watch_with_nothing_to_watch_waits_for_a_folder(tmp_path: Path) -> None:
    """Prepare a session whose skill folders do not exist yet.

    Do create the project's `.vibe/skills`, then write a skill into it.

    Assert the watch picks the folder up and reports the skill, rather than
    ending for good because there was nothing to watch at first.
    """
    project = tmp_path / "project"
    files = _trusted(project)
    changes = watch_skill_changes(
        lambda: skill_watch_roots(files), debounce_ms=50, idle_poll_s=0.1
    )
    first = asyncio.ensure_future(anext(changes))
    await asyncio.sleep(0.3)
    assert not first.done()

    skills = project / ".vibe" / "skills"
    skills.mkdir(parents=True)
    await asyncio.sleep(0.5)
    skill = skills / "review" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: review\ndescription: x\n---\n", encoding="utf-8")

    await asyncio.wait_for(first, timeout=10)
    await changes.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_configured_skill_path_created_later_is_picked_up(
    tmp_path: Path,
) -> None:
    """Prepare a configured skill path whose folder does not exist yet.

    Do create the folder, then write a skill into it.

    Assert the watch reports the change instead of waiting for another one.
    """
    configured = tmp_path / "team" / "skills"
    configured.parent.mkdir()
    files = HarnessFilesManager(sources=())
    changes = watch_skill_changes(
        lambda: skill_watch_roots(files, configured=[configured]), debounce_ms=50
    )
    first = asyncio.ensure_future(anext(changes))
    await asyncio.sleep(0.5)

    configured.mkdir()

    await asyncio.wait_for(first, timeout=10)
    second = asyncio.ensure_future(anext(changes))
    await asyncio.sleep(0.5)
    skill = configured / "review" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: review\ndescription: x\n---\n", encoding="utf-8")

    await asyncio.wait_for(second, timeout=10)
    await changes.aclose()
