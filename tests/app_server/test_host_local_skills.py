"""``skills/local/*``: the user's own skill folders, read and written as files.

Desktop's Skills page opens a local skill in the same editor as a cloud one, so
the host has to hand over a skill's body and files and take edits back without
losing what the editor does not show.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
import os
from pathlib import Path
import stat
import tomllib
from typing import Any, cast

import pytest
import tomli_w

from tests.stubs.process_services import fake_process_services
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._local_skills import MAX_ASSET_BYTES, MAX_SKIPPED_PATHS
from vibe.app_server.protocol import (
    LocalSkillView,
    ProtocolErrorCode,
    SkillsListParams,
    SkillsListResponse,
    SkillsLocalListResponse,
    SkillsLocalReadResponse,
    SkillsLocalWriteResponse,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layer import ConfigStorageError

_CLIENT = ClientInfo(name="test-client", version="0.0.0")


def _handler() -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
    )


async def _read(name: str) -> LocalSkillView:
    result = await _handler().dispatch("skills/local/read", {"name": name})
    return cast(SkillsLocalReadResponse, result.response).skill


async def _write(**params: Any) -> LocalSkillView:
    result = await _handler().dispatch("skills/local/write", params)
    return cast(SkillsLocalWriteResponse, result.response).skill


async def _failure(method: str, params: dict[str, Any]) -> ProtocolErrorCode:
    with pytest.raises(RequestFailure) as caught:
        await _handler().dispatch(method, params)
    return caught.value.code


async def _refusal(method: str, params: dict[str, Any]) -> str | None:
    with pytest.raises(RequestFailure) as caught:
        await _handler().dispatch(method, params)
    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    data = cast(dict[str, Any] | None, caught.value.data)
    return None if data is None else data["reason"]


@pytest.mark.asyncio
async def test_creates_a_skill_that_sessions_then_offer(config_dir: Path) -> None:
    """Prepare nothing on disk.

    Do create a skill with a body and a script.

    Assert it lands in the user's skills folder and `skills/list` offers it.
    """
    created = await _write(
        name="code-review",
        description="Review a diff.",
        body="# Review\n\nRead the diff.",
        assets={"scripts/run.sh": "echo hi\n"},
    )

    skill_dir = config_dir / "skills" / "code-review"
    assert created.path == str(skill_dir)
    assert (skill_dir / "scripts" / "run.sh").read_text() == "echo hi\n"
    listed = await _handler().dispatch(
        "skills/list",
        SkillsListParams().model_dump(mode="json", by_alias=True),
        client_info=_CLIENT,
    )
    names = [s.name for s in cast(SkillsListResponse, listed.response).skills]
    assert "code-review" in names


@pytest.mark.asyncio
async def test_reads_back_what_was_written() -> None:
    created = await _write(
        name="code-review",
        description="Review a diff.",
        body="# Review\n\nRead the diff.",
        assets={"notes.md": "Be kind."},
    )

    read = await _read("code-review")

    assert read == created
    assert read.body == "# Review\n\nRead the diff."
    assert read.assets == {"notes.md": "Be kind."}


@pytest.mark.asyncio
async def test_an_edit_keeps_frontmatter_the_editor_does_not_own(
    config_dir: Path,
) -> None:
    """Prepare a skill whose frontmatter carries keys the editor never shows.

    Do save a new description and body through the editor's write.

    Assert those keys survive and only what the editor owns changed.
    """
    skill_file = config_dir / "skills" / "code-review" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        "---\nname: code-review\ndescription: Old.\nallowed-tools: bash\n"
        "user-invocable: false\n---\n\nOld body.\n"
    )
    opened = await _read("code-review")

    await _write(
        name="code-review",
        description="New.",
        body="New body.",
        expectedVersion=opened.version,
    )

    text = skill_file.read_text()
    assert "allowed-tools: bash" in text
    assert "user-invocable: false" in text
    assert "description: New." in text
    assert text.endswith("New body.\n")


@pytest.mark.asyncio
async def test_refuses_to_overwrite_an_edit_made_on_disk(config_dir: Path) -> None:
    """Prepare a skill opened in the editor, then edited in a text editor.

    Do save from the editor with the version it opened.

    Assert the save is refused as a conflict and the disk edit is kept.
    """
    opened = await _write(name="code-review", description="Review.", body="One.")
    skill_file = config_dir / "skills" / "code-review" / "SKILL.md"
    skill_file.write_text(skill_file.read_text().replace("One.", "Edited elsewhere."))

    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Two.",
            "expectedVersion": opened.version,
        },
    )

    assert code is ProtocolErrorCode.CONFLICT
    assert "Edited elsewhere." in skill_file.read_text()


@pytest.mark.asyncio
async def test_a_save_keeps_the_body_s_leading_indentation() -> None:
    body = "    indented code\n\n```\nfenced\n```"
    opened = await _write(name="code-review", description="Review.", body=body)

    await _write(
        name="code-review",
        description="Review.",
        body=opened.body,
        expectedVersion=opened.version,
    )

    assert (await _read("code-review")).body == body


@pytest.mark.asyncio
async def test_only_one_of_two_saves_from_the_same_version_lands() -> None:
    opened = await _write(name="code-review", description="Review.", body="One.")
    handler = _handler()

    def save(body: str) -> Any:
        return handler.dispatch(
            "skills/local/write",
            {
                "name": "code-review",
                "description": "Review.",
                "body": body,
                "expectedVersion": opened.version,
            },
        )

    results = await asyncio.gather(save("Two."), save("Three."), return_exceptions=True)

    failures = [r for r in results if isinstance(r, RequestFailure)]
    assert len(failures) == 1
    assert failures[0].code is ProtocolErrorCode.CONFLICT
    [saved] = [r for r in results if not isinstance(r, BaseException)]
    landed = cast(SkillsLocalWriteResponse, saved.response).skill
    assert landed == await _read("code-review")


@pytest.mark.asyncio
async def test_a_leftover_folder_does_not_block_the_name_in_the_other_root(
    tmp_path: Path,
) -> None:
    """Prepare a project whose `.vibe/skills/triage` is an empty leftover.

    Do create `triage`, which lands in `.agents/skills`.

    Assert it is created: only a skill sessions load takes the name.
    """
    project = tmp_path / "repo"
    (project / ".vibe" / "skills" / "triage").mkdir(parents=True)

    created = await _write(
        name="triage", description="d", body="b", projectRoot=str(project)
    )

    assert created.path == str(project / ".agents" / "skills" / "triage")


@pytest.mark.asyncio
async def test_create_does_not_replace_a_dangling_link(
    tmp_path: Path, config_dir: Path
) -> None:
    link = config_dir / "skills" / "triage"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(tmp_path / "gone", target_is_directory=True)

    reason = await _refusal(
        "skills/local/write", {"name": "triage", "description": "d", "body": "b"}
    )

    assert reason == "in_the_way"
    assert link.is_symlink()


@pytest.mark.asyncio
async def test_what_an_interrupted_save_leaves_is_not_shown(config_dir: Path) -> None:
    """Prepare a skill next to a staging folder, and a temp file in the skill.

    Do list and read.

    Assert neither shows up as a skill or a file.
    """
    await _write(name="triage", description="d", body="b")
    skills = config_dir / "skills"
    staging = skills / ".triage.0123456789abcdef"
    staging.mkdir()
    (staging / "SKILL.md").write_text("---\nname: triage\ndescription: d\n---\n")
    (skills / "triage" / ".notes.md.0123456789abcdef").write_text("half")

    listed = await _handler().dispatch("skills/local/list", {})
    read = await _read("triage")

    names = [s.name for s in cast(SkillsLocalListResponse, listed.response).skills]
    assert names == ["triage"]
    assert read.assets == {}
    assert read.skipped_paths == []


@pytest.mark.asyncio
async def test_refuses_two_files_that_differ_only_in_case(config_dir: Path) -> None:
    code = await _failure(
        "skills/local/write",
        {
            "name": "triage",
            "description": "d",
            "body": "b",
            "assets": {"Notes.md": "One.", "notes.md": "Two."},
        },
    )

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert not (config_dir / "skills" / "triage").exists()


@pytest.mark.asyncio
async def test_creating_over_an_existing_skill_is_a_conflict() -> None:
    await _write(name="code-review", description="Review.", body="One.")

    code = await _failure(
        "skills/local/write",
        {"name": "code-review", "description": "Review.", "body": "Two."},
    )

    assert code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_removing_a_file_deletes_it_and_keeps_files_the_editor_skipped(
    config_dir: Path,
) -> None:
    """Prepare a skill with a text file in a folder and a binary file.

    Do save it without the text file.

    Assert the text file and its emptied folder are gone, the binary untouched.
    """
    skill_dir = config_dir / "skills" / "code-review"
    opened = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={"refs/guide.md": "Guide."},
    )
    (skill_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    reopened = await _read("code-review")
    assert reopened.skipped_paths == ["logo.png"]
    assert reopened.version != opened.version

    await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={},
        expectedVersion=reopened.version,
    )

    assert not (skill_dir / "refs").exists()
    assert (skill_dir / "logo.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_renaming_a_file_by_case_only_keeps_it(config_dir: Path) -> None:
    """Prepare a skill with `notes.md`.

    Do save it with that file renamed to `Notes.md`.

    Assert the file is there with its content. On macOS and Windows both names
    are one file, so removing the old name after writing the new one deleted it.
    """
    opened = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={"notes.md": "Notes."},
    )

    saved = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={"Notes.md": "Notes."},
        expectedVersion=opened.version,
    )

    assert saved.assets == {"Notes.md": "Notes."}
    skill_dir = config_dir / "skills" / "code-review"
    assert [
        path.name for path in skill_dir.glob("*.md") if path.name != "SKILL.md"
    ] == ["Notes.md"]


@pytest.mark.asyncio
async def test_a_skill_with_many_unreadable_files_lists_only_the_first(
    config_dir: Path,
) -> None:
    await _write(name="code-review", description="Review.", body="Body.")
    blobs = config_dir / "skills" / "code-review" / "blobs"
    blobs.mkdir()
    for index in range(MAX_SKIPPED_PATHS + 5):
        (blobs / f"{index:03}.bin").write_bytes(b"\xff\xfe")

    skill = await _read("code-review")

    assert len(skill.skipped_paths) == MAX_SKIPPED_PATHS


@pytest.mark.asyncio
async def test_a_save_lands_even_when_an_emptied_folder_cannot_be_removed(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={"refs/guide.md": "Guide."},
    )

    def refuse(_: Path) -> None:
        raise PermissionError("busy")

    monkeypatch.setattr(Path, "rmdir", refuse)
    saved = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        expectedVersion=opened.version,
    )

    assert saved.assets == {}
    assert saved == await _read("code-review")


@pytest.mark.asyncio
async def test_refuses_a_file_that_would_replace_one_the_editor_skipped(
    config_dir: Path,
) -> None:
    skill_dir = config_dir / "skills" / "code-review"
    await _write(name="code-review", description="Review.", body="Body.")
    (skill_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\xff")
    opened = await _read("code-review")

    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Body.",
            "assets": {"logo.png": "text"},
            "expectedVersion": opened.version,
        },
    )

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert (skill_dir / "logo.png").read_bytes().startswith(b"\x89PNG")


@pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
@pytest.mark.asyncio
async def test_new_files_are_readable_by_others(config_dir: Path) -> None:
    previous = os.umask(0o022)
    try:
        await _write(
            name="code-review",
            description="Review.",
            body="Body.",
            assets={"refs/guide.md": "Guide."},
        )
    finally:
        os.umask(previous)

    skill_dir = config_dir / "skills" / "code-review"
    for path in (skill_dir / "SKILL.md", skill_dir / "refs" / "guide.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        "/etc/passwd",
        "a/../../b",
        "SKILL.md",
        "C:/x.md",
        # Reading skips dot-folders, so the editor could never open it again.
        ".vscode/settings.json",
    ],
)
@pytest.mark.asyncio
async def test_refuses_a_file_path_outside_the_skill(
    path: str, config_dir: Path
) -> None:
    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Body.",
            "assets": {path: "x"},
        },
    )

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert not (config_dir / "skills" / "code-review").exists()


@pytest.mark.parametrize("name", ["../evil", "Not Kebab", "a/b", "", "C:x"])
@pytest.mark.asyncio
async def test_refuses_a_name_that_is_not_a_skill_folder(name: str) -> None:
    code = await _failure(
        "skills/local/write", {"name": name, "description": "d", "body": "b"}
    )

    assert code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_reading_a_missing_skill_is_not_found() -> None:
    assert await _failure("skills/local/read", {"name": "nope"}) is (
        ProtocolErrorCode.NOT_FOUND
    )


@pytest.mark.asyncio
async def test_deletes_a_skill(config_dir: Path) -> None:
    await _write(name="code-review", description="Review.", body="Body.")

    await _handler().dispatch("skills/local/delete", {"name": "code-review"})

    assert not (config_dir / "skills" / "code-review").exists()


@pytest.mark.asyncio
async def test_deleting_a_junctioned_skill_keeps_its_target(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a user skill folder that is a Windows junction to dotfiles.

    Do delete the skill.

    Assert only the junction goes: the dotfiles copy is never walked.
    """
    kept = tmp_path / "dotfiles" / "code-review"
    kept.mkdir(parents=True)
    (kept / "SKILL.md").write_text("---\nname: code-review\ndescription: d\n---\n")
    link = config_dir / "skills" / "code-review"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(kept, target_is_directory=True)
    real_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p != link and real_is_symlink(p))
    monkeypatch.setattr(Path, "is_junction", lambda p: p == link)
    monkeypatch.setattr(os, "rmdir", lambda p: os.unlink(p))

    def no_rmtree(*_: object, **__: object) -> None:
        raise AssertionError("walked into the junction")

    monkeypatch.setattr("vibe.app_server._local_skills.shutil.rmtree", no_rmtree)

    await _handler().dispatch("skills/local/delete", {"name": "code-review"})

    assert not os.path.lexists(link)
    assert (kept / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_skill_recreated_after_deleting_a_disabled_one_starts_on(
    config_dir: Path,
) -> None:
    """Prepare a skill turned off.

    Do delete it, then create a new skill with the same name.

    Assert the new skill is on: its "off" was stored by name and must not
    outlive the skill it was set for.
    """
    await _write(name="code-review", description="Review.", body="Body.")
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "code-review", "enabled": False}
    )

    await _handler().dispatch("skills/local/delete", {"name": "code-review"})
    created = await _write(name="code-review", description="Again.", body="Body.")

    assert created.enabled is True


@pytest.mark.asyncio
async def test_deleting_one_of_two_same_named_skills_keeps_the_other_off(
    tmp_path: Path, config_dir: Path
) -> None:
    await _write(name="code-review", description="Mine.", body="Body.")
    project = tmp_path / "repo"
    project.mkdir()
    await _write(
        name="code-review", description="Team.", body="Body.", projectRoot=str(project)
    )
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "code-review", "enabled": False}
    )

    await _handler().dispatch(
        "skills/local/delete", {"name": "code-review", "projectRoot": str(project)}
    )

    assert (await _read("code-review")).enabled is False


@pytest.mark.asyncio
async def test_deleting_a_repo_skill_keeps_another_checkouts_copy_off(
    tmp_path: Path, config_dir: Path
) -> None:
    repos = [tmp_path / "repo-a", tmp_path / "repo-b"]
    for repo in repos:
        repo.mkdir()
        await _write(
            name="code-review", description="Team.", body="Body.", projectRoot=str(repo)
        )
    await _handler().dispatch(
        "skills/local/setEnabled",
        {"name": "code-review", "enabled": False, "projectRoot": str(repos[0])},
    )

    await _handler().dispatch(
        "skills/local/delete",
        {
            "name": "code-review",
            "projectRoot": str(repos[0]),
            "otherProjectRoots": [str(repos[1]), str(tmp_path / "moved-away")],
        },
    )

    result = await _handler().dispatch(
        "skills/local/read", {"name": "code-review", "projectRoot": str(repos[1])}
    )
    assert cast(SkillsLocalReadResponse, result.response).skill.enabled is False


@pytest.mark.asyncio
async def test_lists_the_users_skill_folders(config_dir: Path) -> None:
    """Prepare a skill in each user folder, one name in both, and a broken one.

    Do list local skills.

    Assert each name appears once, from `~/.vibe/skills` first, and the folder
    that does not parse is left out.
    """
    agents_skills = config_dir.parent / ".agents" / "skills"
    for root, name, description in [
        (config_dir / "skills", "code-review", "Vibe copy."),
        (agents_skills, "code-review", "Agents copy."),
        (agents_skills, "release-notes", "Write notes."),
    ]:
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n"
        )
    (agents_skills / "broken").mkdir()
    (agents_skills / "broken" / "SKILL.md").write_text("no frontmatter")

    result = await _handler().dispatch("skills/local/list", {})

    skills = cast(SkillsLocalListResponse, result.response).skills
    assert [(s.name, s.description) for s in skills] == [
        ("code-review", "Vibe copy."),
        ("release-notes", "Write notes."),
    ]


@pytest.mark.asyncio
async def test_edits_a_skill_where_it_lives(config_dir: Path) -> None:
    """Prepare a skill only in `~/.agents/skills`.

    Do open and save it.

    Assert the save lands in that folder, not a new copy in `~/.vibe/skills`.
    """
    skill_dir = config_dir.parent / ".agents" / "skills" / "release-notes"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release-notes\ndescription: Old.\n---\n\nOld.\n"
    )
    opened = await _read("release-notes")

    await _write(
        name="release-notes",
        description="New.",
        body="New.",
        expectedVersion=opened.version,
    )

    assert "description: New." in (skill_dir / "SKILL.md").read_text()
    assert not (config_dir / "skills" / "release-notes").exists()


@pytest.mark.asyncio
async def test_turning_a_skill_off_hides_it_from_sessions(config_dir: Path) -> None:
    """Prepare a local skill sessions offer.

    Do turn it off, then back on, from the Skills page.

    Assert it leaves and rejoins `skills/list` through the user config, while
    the folder stays on disk.
    """
    await _write(name="code-review", description="Review.", body="Body.")

    async def offered() -> bool:
        listed = await _handler().dispatch(
            "skills/list",
            SkillsListParams().model_dump(mode="json", by_alias=True),
            client_info=_CLIENT,
        )
        return any(
            s.name == "code-review"
            for s in cast(SkillsListResponse, listed.response).skills
        )

    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "code-review", "enabled": False}
    )
    assert not await offered()
    assert "code-review" in (config_dir / "config.toml").read_text()
    assert (await _read("code-review")).enabled is False

    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "code-review", "enabled": True}
    )
    assert await offered()
    assert (config_dir / "skills" / "code-review" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_skill_a_pattern_disables_cannot_be_toggled(config_dir: Path) -> None:
    """Prepare a skill the user config disables through a pattern.

    Do list it, then try to turn it on.

    Assert it is reported off and locked, and the toggle is refused.
    """
    await _write(name="code-review", description="Review.", body="Body.")
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text())
    config_file.write_text(tomli_w.dumps({**config, "disabled_skills": ["code-*"]}))

    result = await _handler().dispatch("skills/local/list", {})
    [skill] = cast(SkillsLocalListResponse, result.response).skills
    assert (skill.enabled, skill.locked) == (False, True)
    reason = await _refusal(
        "skills/local/setEnabled", {"name": "code-review", "enabled": True}
    )
    assert reason == "locked"


@pytest.mark.asyncio
async def test_lists_and_edits_a_projects_skills_in_place(tmp_path: Path) -> None:
    """Prepare a project with one skill in `.vibe/skills` and one in `.agents/skills`.

    Do list them, edit the `.vibe` one, and create a new one.

    Assert both are listed, the edit stays in `.vibe/skills`, and a new skill
    goes to `.agents/skills` where every agent harness reads it.
    """
    project = tmp_path / "repo"
    for folder, name in [(".vibe", "code-review"), (".agents", "release-notes")]:
        skill = project / folder / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(f"---\nname: {name}\ndescription: Old.\n---\n\nOld.\n")
    root = str(project)

    listed = await _handler().dispatch("skills/local/list", {"projectRoot": root})
    names = [s.name for s in cast(SkillsLocalListResponse, listed.response).skills]
    assert names == ["code-review", "release-notes"]

    opened = await _handler().dispatch(
        "skills/local/read", {"name": "code-review", "projectRoot": root}
    )
    await _write(
        name="code-review",
        description="New.",
        body="New.",
        expectedVersion=cast(SkillsLocalReadResponse, opened.response).skill.version,
        projectRoot=root,
    )
    await _write(name="triage", description="Triage.", body="Body.", projectRoot=root)

    vibe_skill = project / ".vibe" / "skills" / "code-review" / "SKILL.md"
    assert "description: New." in vibe_skill.read_text()
    assert (project / ".agents" / "skills" / "triage" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_projects_own_config_decides_its_skills(tmp_path: Path) -> None:
    """Prepare a project whose `.vibe/config.toml` disables its skill by pattern.

    Do list the project's skills.

    Assert the skill reads as off and locked, as sessions there see it.
    """
    project = tmp_path / "repo"
    skill = project / ".agents" / "skills" / "release-notes" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: release-notes\ndescription: d\n---\n\nb\n")
    (project / ".vibe").mkdir()
    (project / ".vibe" / "config.toml").write_text('disabled_skills = ["release-*"]\n')
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
    )

    listed = await handler.dispatch(
        "skills/local/list", {"projectRoot": str(project), "trustWorkspace": True}
    )

    [summary] = cast(SkillsLocalListResponse, listed.response).skills
    assert summary.enabled is False
    assert summary.locked is True


@pytest.mark.asyncio
async def test_the_users_skills_ignore_the_project_the_server_runs_in(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a trusted project that disables `code-review`, and run from it.

    Assert the user's own `code-review` still reads as on: their own skills
    follow their own config.
    """
    from vibe.core.trusted_folders import trusted_folders_manager

    await _write(name="code-review", description="Mine.", body="Body.")
    project = tmp_path / "repo"
    (project / ".vibe").mkdir(parents=True)
    (project / ".vibe" / "config.toml").write_text(
        'disabled_skills = ["code-review"]\n'
    )
    trusted_folders_manager.trust_for_session(project)
    monkeypatch.chdir(project)
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
    )

    result = await handler.dispatch("skills/local/read", {"name": "code-review"})

    skill = cast(SkillsLocalReadResponse, result.response).skill
    assert skill.enabled is True
    assert skill.locked is False


@pytest.mark.asyncio
async def test_an_untrusted_projects_config_is_not_read(tmp_path: Path) -> None:
    """Prepare a project whose config disables its skill, and do not trust it.

    Assert its config is ignored: only the caller can decide to trust it.
    """
    project = tmp_path / "repo"
    skill = project / ".agents" / "skills" / "release-notes" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: release-notes\ndescription: d\n---\n\nb\n")
    (project / ".vibe").mkdir()
    (project / ".vibe" / "config.toml").write_text('disabled_skills = ["release-*"]\n')
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
    )

    listed = await handler.dispatch("skills/local/list", {"projectRoot": str(project)})

    [summary] = cast(SkillsLocalListResponse, listed.response).skills
    assert summary.enabled is True


@pytest.mark.asyncio
async def test_a_project_and_the_user_keep_separate_skills(
    tmp_path: Path, config_dir: Path
) -> None:
    await _write(name="code-review", description="Mine.", body="Body.")
    project = tmp_path / "repo"
    project.mkdir()

    await _write(
        name="code-review", description="Team.", body="Body.", projectRoot=str(project)
    )

    assert (config_dir / "skills" / "code-review").is_dir()
    assert (project / ".agents" / "skills" / "code-review").is_dir()


@pytest.mark.asyncio
async def test_refuses_a_project_root_that_is_not_a_folder(tmp_path: Path) -> None:
    reason = await _refusal(
        "skills/local/list", {"projectRoot": str(tmp_path / "missing")}
    )

    assert reason == "not_a_project_folder"


@pytest.mark.asyncio
async def test_a_project_skill_folder_linked_outside_the_project_is_refused(
    tmp_path: Path,
) -> None:
    """Prepare a project whose `.agents/skills` links to a folder outside it.

    Do list, read, create and delete through that link.

    Assert the linked skill is not listed or found, a new one is refused, and
    nothing outside the project changes.
    """
    outside = tmp_path / "elsewhere"
    (outside / "release-notes").mkdir(parents=True)
    outside_skill = outside / "release-notes" / "SKILL.md"
    outside_skill.write_text("---\nname: release-notes\ndescription: d\n---\n\nb\n")
    project = tmp_path / "repo"
    (project / ".agents").mkdir(parents=True)
    (project / ".agents" / "skills").symlink_to(outside, target_is_directory=True)
    root = str(project)

    listed = await _handler().dispatch("skills/local/list", {"projectRoot": root})
    assert cast(SkillsLocalListResponse, listed.response).skills == []
    for method, params, expected in [
        ("skills/local/read", {"name": "release-notes"}, ProtocolErrorCode.NOT_FOUND),
        ("skills/local/delete", {"name": "release-notes"}, ProtocolErrorCode.NOT_FOUND),
        (
            "skills/local/write",
            {"name": "triage", "description": "d", "body": "b"},
            ProtocolErrorCode.INVALID_PARAMS,
        ),
    ]:
        assert await _failure(method, {**params, "projectRoot": root}) is expected
    assert sorted(p.name for p in outside.iterdir()) == ["release-notes"]
    assert outside_skill.is_file()


@pytest.mark.asyncio
async def test_a_link_out_of_the_project_does_not_hide_the_repo_copy(
    tmp_path: Path,
) -> None:
    """Prepare `.vibe/skills/notes` linking outside the project, and a real
    `.agents/skills/notes` in it.

    Assert the real one is the skill read, saved and deleted, as listing shows.
    """
    outside = tmp_path / "elsewhere" / "notes"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text("---\nname: notes\ndescription: x\n---\n\nx\n")
    project = tmp_path / "repo"
    (project / ".vibe" / "skills").mkdir(parents=True)
    (project / ".vibe" / "skills" / "notes").symlink_to(
        outside, target_is_directory=True
    )
    real = project / ".agents" / "skills" / "notes"
    real.mkdir(parents=True)
    (real / "SKILL.md").write_text("---\nname: notes\ndescription: d\n---\n\nb\n")
    root = str(project)

    opened = cast(
        SkillsLocalReadResponse,
        (
            await _handler().dispatch(
                "skills/local/read", {"name": "notes", "projectRoot": root}
            )
        ).response,
    ).skill
    await _write(
        name="notes",
        description="Edited.",
        body="b",
        expectedVersion=opened.version,
        projectRoot=root,
    )
    await _handler().dispatch(
        "skills/local/delete", {"name": "notes", "projectRoot": root}
    )

    assert opened.description == "d"
    assert not real.exists()
    assert "description: x" in (outside / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_a_save_that_fails_on_a_file_leaves_the_skill_as_it_was(
    config_dir: Path,
) -> None:
    """Prepare a skill, and a folder where the save would write a file.

    Do save a new body with that file.

    Assert the save fails and the skill keeps its body and version, so the
    editor can retry against what it opened.
    """
    opened = await _write(name="code-review", description="Review.", body="Body.")
    (config_dir / "skills" / "code-review" / "guide.md").mkdir()

    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "New body.",
            "assets": {"guide.md": "Guide."},
            "expectedVersion": opened.version,
        },
    )

    assert code is ProtocolErrorCode.INVALID_PARAMS
    reread = await _read("code-review")
    assert reread.body == "Body."
    assert reread.version == opened.version


@pytest.mark.asyncio
@pytest.mark.parametrize("description", ["yes", "123", "---", "Use it.\n---\nAlways."])
async def test_a_description_reads_back_as_written(description: str) -> None:
    created = await _write(name="code-review", description=description, body="Body.")

    assert created.description == description
    assert created.body == "Body."


@pytest.mark.asyncio
async def test_a_save_that_fails_part_way_puts_back_what_it_changed(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a skill with two files, and a save whose second write fails.

    Assert every file is back as it was, so the version still matches.
    """
    from vibe.app_server import _local_skills

    opened = await _write(
        name="code-review",
        description="Review.",
        body="Body.",
        assets={"a.md": "A.", "old.md": "Old."},
    )
    write = _local_skills._atomic_write

    def failing_write(target: Path, content: str) -> None:
        if target.name == "b.md":
            raise OSError("disk full")
        write(target, content)

    monkeypatch.setattr(_local_skills, "_atomic_write", failing_write)
    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "New body.",
            "assets": {"a.md": "A changed.", "b.md": "B."},
            "expectedVersion": opened.version,
        },
    )
    monkeypatch.setattr(_local_skills, "_atomic_write", write)

    assert code is ProtocolErrorCode.INVALID_PARAMS
    reread = await _read("code-review")
    assert reread.assets == {"a.md": "A.", "old.md": "Old."}
    assert reread.version == opened.version


@pytest.mark.asyncio
async def test_a_project_skill_entry_linked_outside_is_left_out(tmp_path: Path) -> None:
    """Prepare a repo skill folder whose `SKILL.md` links to a file outside it.

    Assert it is not listed or read, so a repo cannot show files from elsewhere.
    """
    outside = tmp_path / "secret.md"
    outside.write_text("---\nname: leak\ndescription: secret\n---\n\nsecret\n")
    project = tmp_path / "repo"
    skill_dir = project / ".agents" / "skills" / "leak"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").symlink_to(outside)
    root = str(project)

    listed = await _handler().dispatch("skills/local/list", {"projectRoot": root})

    assert cast(SkillsLocalListResponse, listed.response).skills == []
    assert await _failure(
        "skills/local/read", {"name": "leak", "projectRoot": root}
    ) is (ProtocolErrorCode.NOT_FOUND)


@pytest.mark.asyncio
async def test_a_create_that_fails_leaves_nothing_behind(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a file write that fails part-way through a create.

    Assert no folder is left, so the same name can be created again.
    """
    from vibe.app_server import _local_skills

    write = _local_skills._atomic_write

    def failing_write(target: Path, content: str) -> None:
        if target.name == "guide.md":
            raise OSError("disk full")
        write(target, content)

    monkeypatch.setattr(_local_skills, "_atomic_write", failing_write)
    code = await _failure(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Body.",
            "assets": {"guide.md": "Guide."},
        },
    )
    monkeypatch.setattr(_local_skills, "_atomic_write", write)

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert list((config_dir / "skills").iterdir()) == []
    created = await _write(name="code-review", description="Review.", body="Body.")
    assert created.name == "code-review"


@pytest.mark.asyncio
async def test_a_linked_file_is_skipped_and_kept(
    tmp_path: Path, config_dir: Path
) -> None:
    """Prepare a skill holding a file that links outside it.

    Do read the skill, then save a file over the link's path.

    Assert the link is listed as skipped and the save leaves it in place.
    """
    await _write(name="code-review", description="Review.", body="Body.")
    outside = tmp_path / "shared.md"
    outside.write_text("Shared.")
    link = config_dir / "skills" / "code-review" / "shared.md"
    link.symlink_to(outside)

    opened = await _read("code-review")
    reason = await _refusal(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Body.",
            "assets": {"shared.md": "Mine."},
            "expectedVersion": opened.version,
        },
    )

    assert opened.skipped_paths == ["shared.md"]
    assert "shared.md" not in opened.assets
    assert reason == "skipped_file"
    assert link.is_symlink()
    assert outside.read_text() == "Shared."


@pytest.mark.asyncio
async def test_a_file_in_a_linked_folder_is_refused(
    tmp_path: Path, config_dir: Path
) -> None:
    """Prepare a skill whose `refs` folder links outside the skill.

    Do save a file into `refs`.

    Assert the save is refused and nothing is written through the link.
    """
    opened = await _write(name="code-review", description="Review.", body="Body.")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    skill_dir = config_dir / "skills" / "code-review"
    (skill_dir / "refs").symlink_to(outside, target_is_directory=True)

    reason = await _refusal(
        "skills/local/write",
        {
            "name": "code-review",
            "description": "Review.",
            "body": "Body.",
            "assets": {"refs/guide.md": "Guide."},
            "expectedVersion": opened.version,
        },
    )

    assert reason is None
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_a_user_skill_linked_from_elsewhere_is_listed_and_editable(
    tmp_path: Path, config_dir: Path
) -> None:
    """Prepare a user skill folder that links to one kept in another folder.

    Do list it, then save it.

    Assert it is listed and the save lands in the linked folder: the user made
    the link, and sessions load the skill through it.
    """
    kept = tmp_path / "dotfiles" / "code-review"
    kept.mkdir(parents=True)
    (kept / "SKILL.md").write_text("---\nname: code-review\ndescription: d\n---\n\nb\n")
    (config_dir / "skills").mkdir(parents=True, exist_ok=True)
    (config_dir / "skills" / "code-review").symlink_to(kept, target_is_directory=True)

    listed = await _handler().dispatch("skills/local/list", {})
    assert [s.name for s in cast(SkillsLocalListResponse, listed.response).skills] == [
        "code-review"
    ]
    opened = await _read("code-review")
    await _write(
        name="code-review",
        description="Edited.",
        body="b",
        expectedVersion=opened.version,
    )

    assert "Edited." in (kept / "SKILL.md").read_text()


@pytest.mark.asyncio
async def test_a_file_system_error_is_invalid_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _write(name="code-review", description="Review.", body="Body.")

    def refuse(path: Path) -> None:
        raise PermissionError(f"Permission denied: {path}")

    monkeypatch.setattr("vibe.app_server._local_skills.shutil.rmtree", refuse)

    reason = await _refusal("skills/local/delete", {"name": "code-review"})

    # A real disk error carries no reason, so Desktop still reports it.
    assert reason is None


def _skill_at(skill_dir: Path, name: str) -> Path:
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(f"---\nname: {name}\ndescription: Old.\n---\n\nOld.\n")
    return skill_file


@pytest.mark.asyncio
async def test_a_skill_named_unlike_its_folder_keeps_its_name(config_dir: Path) -> None:
    """Prepare a folder `foo` whose frontmatter names the skill `bar`.

    Do turn it off, then save it from the editor.

    Assert the toggle disables `bar`, the name sessions know it by, and the
    save leaves the frontmatter name alone.
    """
    skill_file = _skill_at(config_dir / "skills" / "foo", "bar")

    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "foo", "enabled": False}
    )
    config = tomllib.loads((config_dir / "config.toml").read_text())
    assert config["disabled_skills"] == ["bar"]
    opened = await _read("foo")
    assert (opened.name, opened.enabled) == ("foo", False)

    await _write(
        name="foo", description="New.", body="New.", expectedVersion=opened.version
    )

    assert "name: bar" in skill_file.read_text()
    assert "description: New." in skill_file.read_text()


@pytest.mark.asyncio
async def test_saves_a_skill_whose_folder_breaks_the_naming_rule(
    config_dir: Path,
) -> None:
    skill_file = _skill_at(config_dir / "skills" / "Code_Review", "Code_Review")
    opened = await _read("Code_Review")

    await _write(
        name="Code_Review",
        description="New.",
        body="New.",
        expectedVersion=opened.version,
    )

    assert "description: New." in skill_file.read_text()
    assert "name: Code_Review" in skill_file.read_text()


@pytest.mark.asyncio
async def test_turning_off_a_project_skill_turns_off_the_users_namesake(
    tmp_path: Path,
) -> None:
    """Prepare a user skill and a project skill with the same name.

    Do turn the project's one off.

    Assert the user's one reads as off too: sessions filter skills by name.
    """
    await _write(name="code-review", description="Mine.", body="Body.")
    project = tmp_path / "repo"
    _skill_at(project / ".agents" / "skills" / "code-review", "code-review")

    await _handler().dispatch(
        "skills/local/setEnabled",
        {"name": "code-review", "enabled": False, "projectRoot": str(project)},
    )

    listed = await _handler().dispatch("skills/local/list", {})
    [mine] = cast(SkillsLocalListResponse, listed.response).skills
    assert (mine.name, mine.enabled) == ("code-review", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"name": "Bad_Name"},
        {"name": "triage", "description": ""},
        {"name": "triage", "assets": {"../escape.md": "x"}},
        {"name": "triage", "assets": {".git/config": "x"}},
        {"name": "triage", "assets": {"a.md": "x", "A.md": "y"}},
        {"name": "triage", "assets": {"big.md": "x" * (MAX_ASSET_BYTES + 1)}},
    ],
)
async def test_a_write_the_editor_already_checks_is_refused_without_a_reason(
    params: dict[str, Any],
) -> None:
    """Only refusals the user can learn of from the server alone carry a reason."""
    write = {"description": "d", "body": "b", **params}

    assert await _refusal("skills/local/write", write) is None


@pytest.mark.asyncio
async def test_lists_and_reads_the_name_sessions_know_a_skill_by(
    config_dir: Path,
) -> None:
    """Prepare a folder `foo` whose frontmatter names the skill `bar`.

    Assert list and read report the folder as `name` and `bar` as `skillName`.
    """
    _skill_at(config_dir / "skills" / "foo", "bar")

    listed = await _handler().dispatch("skills/local/list", {})
    [summary] = cast(SkillsLocalListResponse, listed.response).skills
    read = await _read("foo")

    assert (summary.name, summary.skill_name) == ("foo", "bar")
    assert (read.name, read.skill_name) == ("foo", "bar")
    assert summary.model_dump(mode="json")["skillName"] == "bar"


@pytest.mark.asyncio
async def test_creates_a_skill_turned_off(config_dir: Path) -> None:
    created = await _write(
        name="code-review", description="Review.", body="Body.", enabled=False
    )

    assert created.enabled is False
    config = tomllib.loads((config_dir / "config.toml").read_text())
    assert config["disabled_skills"] == ["code-review"]


@pytest.mark.asyncio
async def test_a_skill_created_on_starts_on_over_a_stale_off(config_dir: Path) -> None:
    """Prepare an "off" for `code-review` left in the config with no skill.

    Do create `code-review` with its switch on.

    Assert it starts on: the stale "off" is cleared.
    """
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text())
    config_file.write_text(
        tomli_w.dumps({**config, "disabled_skills": ["code-review"]})
    )

    created = await _write(
        name="code-review", description="Review.", body="Body.", enabled=True
    )

    assert created.enabled is True
    assert "code-review" not in tomllib.loads(config_file.read_text()).get(
        "disabled_skills", []
    )


@pytest.mark.asyncio
async def test_enabled_on_a_save_leaves_the_toggle_alone(config_dir: Path) -> None:
    opened = await _write(name="code-review", description="Review.", body="Body.")
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "code-review", "enabled": False}
    )

    saved = await _write(
        name="code-review",
        description="New.",
        body="Body.",
        expectedVersion=opened.version,
        enabled=True,
    )

    assert saved.enabled is False


@pytest.mark.asyncio
async def test_deleting_a_skill_keeps_the_off_of_its_skill_paths_namesake(
    tmp_path: Path, config_dir: Path
) -> None:
    """Prepare a user skill `foo`, and a `foo` next to a broken skill in `skill_paths`.

    Do turn `foo` off, then delete the user's one.

    Assert `foo` stays off: sessions still load the other one, and the toggle
    is shared by name.
    """
    extra = tmp_path / "extra-skills"
    (extra / "broken").mkdir(parents=True)
    (extra / "broken" / "SKILL.md").write_text("---\nname: [\n---\n")
    _skill_at(extra / "foo", "foo")
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text())
    config_file.write_text(tomli_w.dumps({**config, "skill_paths": [str(extra)]}))
    await _write(name="foo", description="Mine.", body="Body.")
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "foo", "enabled": False}
    )

    await _handler().dispatch("skills/local/delete", {"name": "foo"})

    config = tomllib.loads(config_file.read_text())
    assert config["disabled_skills"] == ["foo"]


@pytest.mark.asyncio
async def test_deleting_a_skill_keeps_the_off_of_a_pinned_namesake(
    config_dir: Path,
) -> None:
    """Prepare a user skill `foo` and a registry pin named `foo`."""
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text())
    config_file.write_text(
        tomli_w.dumps({**config, "experimental_enable_registry_skills": True})
    )
    (config_dir / "skills.toml").write_text(
        tomli_w.dumps({"skills": [{"name": "foo", "skill_id": "skill-1"}]})
    )
    await _write(name="foo", description="Mine.", body="Body.")
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "foo", "enabled": False}
    )

    await _handler().dispatch("skills/local/delete", {"name": "foo"})

    config = tomllib.loads(config_file.read_text())
    assert config["disabled_skills"] == ["foo"]


@pytest.mark.asyncio
async def test_a_create_whose_switch_cannot_be_saved_still_creates_the_skill(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a config that fails to save.

    Do create a skill with its switch off.

    Assert the create succeeds and reports the skill as it starts: on.
    """

    async def refuse(*_args: object, **_kwargs: object) -> None:
        raise ConfigStorageError("user", config_dir / "config.toml", "write")

    monkeypatch.setattr(
        "vibe.app_server._local_skills_controller.set_skill_enabled", refuse
    )

    created = await _write(
        name="code-review", description="Review.", body="Body.", enabled=False
    )

    assert created.enabled is True
    assert (config_dir / "skills" / "code-review" / "SKILL.md").is_file()


@pytest.mark.asyncio
async def test_a_delete_succeeds_when_skill_paths_cannot_be_read(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prepare a turned-off skill, and a `skill_paths` folder that cannot be listed.

    Do delete the skill.

    Assert the delete succeeds and keeps the "off": an unread folder may still
    hold a namesake.
    """
    extra = tmp_path / "extra-skills"
    extra.mkdir()
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text())
    config_file.write_text(tomli_w.dumps({**config, "skill_paths": [str(extra)]}))
    await _write(name="foo", description="Mine.", body="Body.")
    await _handler().dispatch(
        "skills/local/setEnabled", {"name": "foo", "enabled": False}
    )
    real_iterdir = Path.iterdir

    def refuse(path: Path) -> Iterator[Path]:
        if path == extra:
            raise PermissionError(13, "Permission denied", str(path))
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", refuse)

    await _handler().dispatch("skills/local/delete", {"name": "foo"})

    assert not (config_dir / "skills" / "foo").exists()
    config = tomllib.loads(config_file.read_text())
    assert config["disabled_skills"] == ["foo"]
