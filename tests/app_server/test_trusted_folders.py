from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import cast
from unittest.mock import patch

import pytest

from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._trusted_folders import (
    MAX_SUGGESTIONS,
    add_trusted_folder,
    dispatch_trust_folders,
    inspect_trust_folder,
    update_trusted_folder,
)
from vibe.app_server._workspace import WorkspaceTrustError
from vibe.app_server.protocol import (
    WorkspaceTrustFoldersInspectResponse,
    WorkspaceTrustFoldersResponse,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import TrustedFoldersManager, trusted_folders_manager


def _resolved(path: Path) -> str:
    return str(path.resolve())


def _handler(
    harness_files: HarnessFilesManager, process_services: ProcessServices
) -> HostRequestHandler:
    return HostRequestHandler(
        harness_files,
        org_config=process_services.org_config,
        worktrees=process_services.worktrees,
        experiments=process_services.experiments,
        model_catalog=process_services.model_catalog,
    )


@pytest.mark.asyncio
async def test_host_lists_updates_and_adds_trusted_folders(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    for name in ("repo", "other", "new"):
        (tmp_path / name).mkdir()
    trust_store = TrustedFoldersManager()
    trust_store.add_trusted(tmp_path / "repo")
    trust_store.add_untrusted(tmp_path / "other")
    repo, other = _resolved(tmp_path / "repo"), _resolved(tmp_path / "other")
    handler = _handler(
        HarnessFilesManager(sources=("user",), trust_store=trust_store),
        process_services,
    )

    listed = await handler.dispatch("workspace/trust/folders/list", {})
    moved = await handler.dispatch(
        "workspace/trust/folders/update", {"path": repo, "state": "untrusted"}
    )
    dropped = await handler.dispatch(
        "workspace/trust/folders/update", {"path": other, "state": "undecided"}
    )
    added = await handler.dispatch(
        "workspace/trust/folders/add", {"path": "new", "cwd": str(tmp_path)}
    )

    assert listed.response.model_dump(mode="json", by_alias=True) == {
        "trusted": [repo],
        "untrusted": [other],
        "settingsPath": str(TRUSTED_FOLDERS_FILE.path),
    }
    assert cast(WorkspaceTrustFoldersResponse, moved.response).untrusted == [
        other,
        repo,
    ]
    assert cast(WorkspaceTrustFoldersResponse, dropped.response).untrusted == [repo]
    assert cast(WorkspaceTrustFoldersResponse, added.response).trusted == [
        _resolved(tmp_path / "new")
    ]
    assert TrustedFoldersManager().persisted_decisions() == {
        "trusted": [_resolved(tmp_path / "new")],
        "untrusted": [repo],
    }


@pytest.mark.asyncio
async def test_list_reads_writes_made_after_startup(tmp_path: Path) -> None:
    TrustedFoldersManager().add_trusted(tmp_path)

    response = await dispatch_trust_folders(
        "workspace/trust/folders/list", {}, cwd=lambda _: tmp_path
    )

    assert cast(WorkspaceTrustFoldersResponse, response).trusted == [
        _resolved(tmp_path)
    ]


@pytest.mark.asyncio
async def test_edits_never_reach_a_running_session(
    tmp_path: Path, process_services: ProcessServices
) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    for folder in (project, other):
        folder.mkdir()
    trusted_folders_manager.add_trusted(project)
    # Sessions share the process manager's stored lists, as `for_session` copies.
    session = HarnessFilesManager(trust_store=trusted_folders_manager.for_session())
    handler = _handler(
        HarnessFilesManager(
            sources=("user",), trust_store=trusted_folders_manager.for_session()
        ),
        process_services,
    )

    await handler.dispatch(
        "workspace/trust/folders/update",
        {"path": _resolved(project), "state": "untrusted"},
    )
    await handler.dispatch(
        "workspace/trust/folders/add", {"path": str(other), "cwd": str(tmp_path)}
    )

    assert session.trust_store.is_trusted(project) is True
    assert session.trust_store.is_trusted(other) is None
    assert TrustedFoldersManager().persisted_decisions() == {
        "trusted": [_resolved(other)],
        "untrusted": [_resolved(project)],
    }


@pytest.mark.asyncio
async def test_an_unreadable_trust_file_is_reported_and_left_alone(
    tmp_path: Path,
) -> None:
    TRUSTED_FOLDERS_FILE.path.write_text("trusted = [", encoding="utf-8")

    for method, params in (
        ("workspace/trust/folders/list", {}),
        ("workspace/trust/folders/add", {"path": str(tmp_path)}),
    ):
        with pytest.raises(WorkspaceTrustError, match="Cannot update"):
            await dispatch_trust_folders(method, params, cwd=lambda _: tmp_path)

    assert TRUSTED_FOLDERS_FILE.path.read_text(encoding="utf-8") == "trusted = ["


def test_update_rejects_paths_not_stored(tmp_path: Path) -> None:
    trust_store = TrustedFoldersManager()
    trust_store.add_trusted(tmp_path)

    with pytest.raises(WorkspaceTrustError, match="No trust decision is stored"):
        update_trusted_folder(trust_store, str(tmp_path / "child"), "untrusted")

    assert trust_store.persisted_decisions()["untrusted"] == []


def _values(response: WorkspaceTrustFoldersInspectResponse) -> list[str]:
    return [candidate.value for candidate in response.candidates]


def test_inspect_reports_what_trusting_each_candidate_covers(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "vendor").mkdir(parents=True)
    trust_store = TrustedFoldersManager()
    trust_store.add_trusted(tmp_path)
    trust_store.add_untrusted(project / "vendor")

    response = inspect_trust_folder(trust_store, f"{project}/", tmp_path)

    first, vendor = response.candidates
    assert response.typed_problem is None
    assert first.path == _resolved(project)
    assert first.problem is None
    assert first.stored is None
    assert first.trusted_by == _resolved(tmp_path)
    assert first.untrusted_inside == [_resolved(project / "vendor")]
    assert first.untrusted_inside_count == 1
    assert vendor.value == f"{project}/vendor/"
    assert vendor.stored == "untrusted"


def test_inspect_offers_the_typed_folder_then_its_children(tmp_path: Path) -> None:
    for name in ("api", "web", ".git"):
        (tmp_path / name).mkdir()
    (tmp_path / "notes.txt").write_text("")

    response = inspect_trust_folder(TrustedFoldersManager(), f"{tmp_path}/", tmp_path)

    assert _values(response) == [f"{tmp_path}/", f"{tmp_path}/api/", f"{tmp_path}/web/"]


def test_inspect_completes_a_partial_name_after_the_typed_folder(
    tmp_path: Path,
) -> None:
    for name in ("app", "apps", "api"):
        (tmp_path / name).mkdir()

    response = inspect_trust_folder(
        TrustedFoldersManager(), f"{tmp_path}/app", tmp_path
    )

    assert response.typed_problem is None
    assert _values(response) == [f"{tmp_path}/app/", f"{tmp_path}/apps/"]


@pytest.mark.parametrize(
    ("typed", "problem"), [("nope/deeper", "missing"), ("notes.txt", "not_directory")]
)
def test_inspect_falls_back_to_the_closest_existing_parent(
    tmp_path: Path, typed: str, problem: str
) -> None:
    (tmp_path / "notes.txt").write_text("")
    (tmp_path / "nope-not").mkdir()

    response = inspect_trust_folder(TrustedFoldersManager(), typed, tmp_path)

    assert response.typed_problem == problem
    assert response.candidates[0].path == _resolved(tmp_path)
    assert response.candidates[0].value == f"{_resolved(tmp_path)}/"


def test_inspect_completes_a_missing_name_inside_its_parent(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()

    response = inspect_trust_folder(TrustedFoldersManager(), "we", tmp_path)

    assert response.typed_problem == "missing"
    assert _values(response) == [f"{_resolved(tmp_path)}/", "web/"]


def test_inspect_starts_on_cwd_when_nothing_is_typed(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()

    response = inspect_trust_folder(TrustedFoldersManager(), "", tmp_path)

    assert _values(response) == [f"{_resolved(tmp_path)}/", "web/"]


@pytest.mark.parametrize(
    ("typed", "problem"), [("~", "home"), ("~/..", "home"), ("/", "root")]
)
def test_home_and_root_are_offered_but_cannot_be_added(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str, problem: str
) -> None:
    # A home two levels deep, so the folder above it is not also the root.
    home = tmp_path / "users" / "me"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    trust_store = TrustedFoldersManager()

    response = inspect_trust_folder(trust_store, typed, tmp_path)

    assert response.candidates[0].problem == problem
    with pytest.raises(WorkspaceTrustError):
        add_trusted_folder(trust_store, typed, tmp_path)
    assert trust_store.persisted_decisions()["trusted"] == []


def test_add_refuses_a_path_that_is_not_a_folder(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("")
    trust_store = TrustedFoldersManager()

    for typed in ("missing", "notes.txt"):
        with pytest.raises(WorkspaceTrustError, match="No folder"):
            add_trusted_folder(trust_store, typed, tmp_path)

    assert trust_store.persisted_decisions()["trusted"] == []


def test_inspect_suggests_hidden_folders_only_when_asked(tmp_path: Path) -> None:
    (tmp_path / ".config").mkdir()

    response = inspect_trust_folder(TrustedFoldersManager(), "./.c", tmp_path)

    assert _values(response)[1:] == ["./.config/"]


def test_inspect_skips_a_child_it_cannot_check(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    # A symlink to itself: `DirEntry.is_dir()` raises ELOOP on it.
    (tmp_path / "loop").symlink_to(tmp_path / "loop")

    response = inspect_trust_folder(TrustedFoldersManager(), "./", tmp_path)

    assert _values(response)[1:] == ["./src/"]


@pytest.mark.skipif(
    sys.platform != "win32", reason="drive-relative paths are Windows only"
)
def test_inspect_keeps_the_folder_a_bare_drive_names(tmp_path: Path) -> None:
    # `C:` is the current folder on drive C, while `C:\\` is its root.
    response = inspect_trust_folder(TrustedFoldersManager(), tmp_path.drive, tmp_path)

    assert response.typed_problem is None
    assert response.candidates[0].value == f"{_resolved(tmp_path)}{os.sep}"


def test_inspect_caps_completions(tmp_path: Path) -> None:
    for index in range(MAX_SUGGESTIONS + 2):
        (tmp_path / f"dir{index:02}").mkdir()

    response = inspect_trust_folder(TrustedFoldersManager(), "./", tmp_path)

    assert len(response.candidates) == MAX_SUGGESTIONS + 1


def test_a_stored_home_or_root_cannot_be_flipped_to_trusted(tmp_path: Path) -> None:
    trust_store = TrustedFoldersManager()
    for folder in (Path.home(), Path("/")):
        trust_store.add_untrusted(folder)
        with pytest.raises(WorkspaceTrustError, match="Cannot trust"):
            update_trusted_folder(trust_store, _resolved(folder), "trusted")

    assert trust_store.persisted_decisions()["trusted"] == []


@pytest.mark.parametrize("typed", ["~no-such-user-5780/x", "bad\x00path"])
def test_a_path_no_folder_can_have_is_an_error(tmp_path: Path, typed: str) -> None:
    with pytest.raises(WorkspaceTrustError, match="Not a usable path"):
        inspect_trust_folder(TrustedFoldersManager(), typed, tmp_path)


@pytest.mark.asyncio
async def test_a_failed_write_is_an_error_not_a_success(tmp_path: Path) -> None:
    with (
        patch("vibe.core.trusted_folders.os.replace", side_effect=OSError("ro")),
        pytest.raises(WorkspaceTrustError, match="Cannot update"),
    ):
        await dispatch_trust_folders(
            "workspace/trust/folders/add",
            {"path": str(tmp_path)},
            cwd=lambda _: tmp_path,
        )

    assert TrustedFoldersManager().persisted_decisions()["trusted"] == []


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root"
)
def test_a_path_inside_an_unreadable_folder_is_an_error(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(WorkspaceTrustError, match="Not a usable path"):
            inspect_trust_folder(
                TrustedFoldersManager(), str(locked / "inner" / "x"), tmp_path
            )
    finally:
        locked.chmod(0o700)


def test_a_name_longer_than_the_system_allows_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceTrustError, match="Not a usable path"):
        inspect_trust_folder(TrustedFoldersManager(), "x" * 300, tmp_path)


def test_home_is_refused_whatever_its_spelling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "Home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    other_spelling = tmp_path / "home"
    if not other_spelling.exists():
        pytest.skip("case-sensitive filesystem")

    response = inspect_trust_folder(
        TrustedFoldersManager(), str(other_spelling), tmp_path
    )

    assert response.candidates[0].problem == "home"


@pytest.mark.asyncio
async def test_an_update_answers_with_the_file_as_written(tmp_path: Path) -> None:
    """The list the client shows includes what another Vibe wrote meanwhile."""
    TrustedFoldersManager().add_trusted(tmp_path / "a")
    TrustedFoldersManager().add_untrusted(tmp_path / "b")
    a, b = _resolved(tmp_path / "a"), _resolved(tmp_path / "b")

    response = await dispatch_trust_folders(
        "workspace/trust/folders/update",
        {"path": a, "state": "undecided"},
        cwd=lambda _: tmp_path,
    )

    assert cast(WorkspaceTrustFoldersResponse, response).untrusted == [b]
