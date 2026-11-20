from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tomllib
from typing import Any, cast
from unittest.mock import MagicMock
from uuid import UUID

from git import Actor, Repo
import pytest
import tomli_w

from tests.stubs.process_services import fake_process_services
from vibe.app_server import (
    _local_projects as local_projects_module,
    _local_projects_controller as local_projects_controller_module,
)
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._local_projects import (
    CloudBacking,
    CloudOwner,
    LocalProject,
    LocalProjectError,
    LocalProjectNotFound,
    LocalProjectStore,
    session_project,
)
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._unified_harness_backend_adapter import _folder_stamps
from vibe.app_server.models import PublicSession, PublicSessionProject
from vibe.app_server.protocol import (
    SERVER_METHODS,
    LocalProjectCloudOwner,
    LocalProjectCloudView,
    LocalProjectView,
    ProjectMutationResponse,
    ProjectsListResponse,
    ProtocolErrorCode,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.paths import WORKTREES_DIR
from vibe.core.types import SessionProject
from vibe.core.vibe_code_project import (
    LocalProjectLink,
    ProjectLink,
    RemoteProjectLink,
    VibeProjectsStore,
    project_link_path,
)

_CLIENT = ClientInfo(name="test-client", version="0.0.0")


def _handler() -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
        worktrees=fake_process_services().worktrees,
        experiments=fake_process_services().experiments,
        model_catalog=fake_process_services().model_catalog,
    )


def _store_file(config_dir: Path) -> Path:
    return config_dir / "local_projects.toml"


def _read_store(config_dir: Path) -> dict[str, Any]:
    with _store_file(config_dir).open("rb") as file:
        return tomllib.load(file)


def _write_store(config_dir: Path, data: dict[str, Any]) -> None:
    _store_file(config_dir).write_text(tomli_w.dumps(data), encoding="utf-8")


async def _list() -> list[LocalProjectView]:
    result = await _handler().dispatch("projects/list", {})
    return cast(ProjectsListResponse, result.response).projects


async def _mutate(method: str, params: dict[str, Any]) -> LocalProjectView:
    result = await _handler().dispatch(method, params)
    return cast(ProjectMutationResponse, result.response).project


async def _failure(method: str, params: dict[str, Any]) -> ProtocolErrorCode:
    with pytest.raises(RequestFailure) as caught:
        await _handler().dispatch(method, params)
    return caught.value.code


def _entry(project_id: str, root: Path, **extra: Any) -> dict[str, Any]:
    return {
        "id": project_id,
        "name": root.name,
        "root_paths": [str(root)],
        "created_at": 1,
        "updated_at": 1,
        **extra,
    }


def _hidden(project_id: str, root: Path, **extra: Any) -> dict[str, Any]:
    """A project an older server removed by only stamping it."""
    return _entry(project_id, root, deleted_at=1, **extra)


def _link(config_dir: Path, link: ProjectLink) -> None:
    VibeProjectsStore(config_dir / "projects.toml").upsert_project_link(link)


def _linked_folders(config_dir: Path) -> set[Path]:
    links = VibeProjectsStore(config_dir / "projects.toml").list_project_links()
    return {project_link_path(link) for link in links}


def _local_link(
    root: Path, cloud_id: str = "cloud-1", name: str = "Linked"
) -> ProjectLink:
    return LocalProjectLink(directory_path=root, project_id=cloud_id, project_name=name)


def _backed(cloud_id: str) -> LocalProjectCloudView:
    return LocalProjectCloudView(project_id=cloud_id, state="backed")


def test_project_methods_are_advertised() -> None:
    assert {
        "projects/create",
        "projects/link",
        "projects/list",
        "projects/remove",
        "projects/rename",
        "projects/retireCloud",
        "projects/setCloud",
    }.issubset(SERVER_METHODS)


@pytest.mark.asyncio
async def test_list_without_a_store_file_is_empty_and_writes_nothing(
    config_dir: Path,
) -> None:
    assert await _list() == []
    assert not _store_file(config_dir).exists()


@pytest.mark.asyncio
async def test_create_writes_the_project_and_lists_it(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "textual"
    root.mkdir()

    created = await _mutate(
        "projects/create", {"rootPath": str(root), "name": "Textual"}
    )

    assert str(UUID(created.id)) == created.id
    assert created.name == "Textual"
    assert created.root_paths == [str(root.resolve())]
    assert created.cloud is None
    assert created.created_at == created.updated_at
    assert await _list() == [created]
    assert _read_store(config_dir) == {
        "version": 1,
        "projects": [
            {
                "id": created.id,
                "name": "Textual",
                "root_paths": [str(root.resolve())],
                "created_at": created.created_at,
                "updated_at": created.updated_at,
            }
        ],
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
@pytest.mark.asyncio
async def test_store_file_is_owner_only(config_dir: Path, tmp_path: Path) -> None:
    await _mutate("projects/create", {"rootPath": str(tmp_path)})

    mode = stat.S_IMODE(_store_file(config_dir).stat().st_mode)
    assert mode == 0o600


@pytest.mark.asyncio
async def test_create_names_the_project_after_its_folder(tmp_path: Path) -> None:
    root = tmp_path / "widgets"
    root.mkdir()

    created = await _mutate("projects/create", {"rootPath": str(root)})

    assert created.name == "widgets"


@pytest.mark.asyncio
async def test_projects_report_whether_their_folder_has_commits(tmp_path: Path) -> None:
    plain, empty, committed = (tmp_path / name for name in ("plain", "empty", "repo"))
    plain.mkdir()
    Repo.init(empty).close()
    repo = Repo.init(committed)
    tester = Actor("Tester", "t@example.com")
    repo.index.commit("initial", author=tester, committer=tester)
    repo.close()

    for root in (plain, empty, committed):
        await _mutate("projects/create", {"rootPath": str(root)})

    assert [(p.name, p.has_commits) for p in await _list()] == [
        ("plain", False),
        ("empty", False),
        ("repo", True),
    ]


@pytest.mark.asyncio
async def test_a_folder_can_belong_to_several_projects(tmp_path: Path) -> None:
    first = await _mutate("projects/create", {"rootPath": str(tmp_path)})
    second = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    assert first.id != second.id
    assert [project.id for project in await _list()] == [first.id, second.id]


@pytest.mark.asyncio
@pytest.mark.parametrize("root_path", ["relative/path", "/no/such/folder"])
async def test_create_rejects_a_path_that_is_not_an_absolute_folder(
    config_dir: Path, root_path: str
) -> None:
    code = await _failure("projects/create", {"rootPath": root_path})

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert not _store_file(config_dir).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, "Root"])
async def test_create_rejects_a_disk_root(
    config_dir: Path, tmp_path: Path, name: str | None
) -> None:
    params: dict[str, Any] = {"rootPath": tmp_path.anchor}
    if name is not None:
        params["name"] = name

    with pytest.raises(RequestFailure) as caught:
        await _handler().dispatch("projects/create", params)

    assert caught.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert "disk root" in str(caught.value)
    assert not _store_file(config_dir).exists()


@pytest.mark.asyncio
async def test_create_rejects_a_blank_name(tmp_path: Path) -> None:
    code = await _failure("projects/create", {"rootPath": str(tmp_path), "name": " "})

    assert code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_rename_keeps_everything_else_including_unknown_fields(
    config_dir: Path, tmp_path: Path
) -> None:
    cloud = {"project_id": "cloud-1", "state": "backed"}
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_1", tmp_path, cloud=cloud, archived_at=5),
                _entry("lp_2", tmp_path),
            ],
        },
    )

    renamed = await _mutate("projects/rename", {"id": "lp_1", "name": " Renamed "})

    assert renamed.name == "Renamed"
    assert renamed.root_paths == [str(tmp_path)]
    assert renamed.created_at == 1
    assert renamed.updated_at > 1
    assert renamed.cloud is not None
    assert renamed.cloud.project_id == "cloud-1"
    assert renamed.cloud.state == "backed"
    stored = _read_store(config_dir)["projects"]
    assert stored[0] == {
        **_entry("lp_1", tmp_path, cloud=cloud, archived_at=5),
        "name": "Renamed",
        "updated_at": renamed.updated_at,
    }
    assert stored[1] == _entry("lp_2", tmp_path)


@pytest.mark.asyncio
async def test_remove_deletes_the_project_and_keeps_its_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    keep = await _mutate("projects/create", {"rootPath": str(tmp_path)})
    gone = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    await _handler().dispatch("projects/remove", {"id": gone.id})

    assert await _list() == [keep]
    assert tmp_path.is_dir()
    assert [entry["id"] for entry in _read_store(config_dir)["projects"]] == [keep.id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("projects/rename", {"name": "x"}),
        ("projects/remove", {}),
        ("projects/setCloud", {"cloudProjectId": "cloud-1"}),
    ],
)
async def test_a_removed_project_is_not_found(
    tmp_path: Path, method: str, params: dict[str, Any]
) -> None:
    gone = await _mutate("projects/create", {"rootPath": str(tmp_path)})
    await _handler().dispatch("projects/remove", {"id": gone.id})

    assert (
        await _failure(method, {"id": gone.id, **params}) is ProtocolErrorCode.NOT_FOUND
    )


@pytest.mark.asyncio
async def test_remove_unlinks_a_backed_projects_folder_so_it_is_not_imported_back(
    config_dir: Path, tmp_path: Path
) -> None:
    _link(config_dir, _local_link(tmp_path.resolve()))
    [imported] = await _list()
    await _handler().dispatch("projects/remove", {"id": imported.id})

    assert await _list() == []
    assert _read_store(config_dir)["projects"] == []
    assert _linked_folders(config_dir) == set()


@pytest.mark.asyncio
async def test_remove_unlinks_a_folder_linked_to_its_cloud_project_since_the_last_listing(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    first, later = root / "first", root / "later"
    first.mkdir()
    later.mkdir()
    _link(config_dir, _local_link(first))
    [imported] = await _list()
    _link(config_dir, _local_link(later))

    await _handler().dispatch("projects/remove", {"id": imported.id})

    assert await _list() == []
    assert _linked_folders(config_dir) == set()


@pytest.mark.asyncio
async def test_remove_succeeds_when_the_import_merges_the_project_away(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    backed, unbacked = root / "backed", root / "unbacked"
    backed.mkdir()
    unbacked.mkdir()
    _link(config_dir, _local_link(backed))
    [backing] = await _list()
    gone = await _mutate("projects/create", {"rootPath": str(unbacked)})
    _link(config_dir, _local_link(unbacked))

    await _handler().dispatch("projects/remove", {"id": gone.id})

    [project] = await _list()
    assert project.id == backing.id


@pytest.mark.asyncio
async def test_a_listing_racing_a_removal_does_not_import_the_project_back(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _link(config_dir, _local_link(tmp_path.resolve()))
    [imported] = await _list()
    store = LocalProjectStore(_store_file(config_dir))
    removed: list[str] = []

    class RemovedAfterReading(VibeProjectsStore):
        def read_project_links(self) -> tuple[list[ProjectLink], bool]:
            links = super().read_project_links()
            if not removed:
                removed.append(imported.id)
                store.remove(imported.id, VibeProjectsStore())
            return links

    monkeypatch.setattr(
        local_projects_controller_module, "VibeProjectsStore", RemovedAfterReading
    )

    assert await _list() == []
    assert removed == [imported.id]
    assert _read_store(config_dir)["projects"] == []


@pytest.mark.asyncio
async def test_remove_keeps_a_link_another_project_on_the_folder_uses(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    backed = {"project_id": "cloud-1", "state": "backed"}
    _link(config_dir, _local_link(root))
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_gone", root, cloud={**backed, "state": "retired"}),
                _entry("lp_kept", root, cloud=backed),
            ],
        },
    )

    await _handler().dispatch("projects/remove", {"id": "lp_gone"})

    assert [entry["id"] for entry in _read_store(config_dir)["projects"]] == ["lp_kept"]
    assert _linked_folders(config_dir) == {root}


@pytest.mark.asyncio
async def test_a_folder_linked_to_a_removed_projects_cloud_project_gets_its_own_project(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    first = root / "first"
    later = root / "later"
    first.mkdir()
    later.mkdir()
    _link(config_dir, _local_link(first))
    [removed] = await _list()
    await _handler().dispatch("projects/remove", {"id": removed.id})
    _link(config_dir, _local_link(later))

    [project] = await _list()

    assert project.id != removed.id
    assert (project.root_paths, project.cloud) == ([str(later)], _backed("cloud-1"))


def test_a_removed_projects_sessions_join_the_project_on_their_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    store = LocalProjectStore(_store_file(config_dir))
    removed = store.create(name="Removed", root_path=root)
    store.remove(removed.id, VibeProjectsStore(config_dir / "projects.toml"))
    live = store.create(name="Live", root_path=root)
    stamped = PublicSession.model_construct(
        id="stamped",
        cwd=str(root),
        project=PublicSessionProject(kind="local", id=removed.id),
    )

    stamps = _folder_stamps(store.list_projects(), store.project_ids(), [stamped])

    assert stamps == {"stamped": SessionProject(kind="local", id=live.id)}


@pytest.mark.asyncio
async def test_list_purges_the_projects_an_older_server_hid_and_their_links(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    first, second, other = root / "first", root / "second", root / "other"
    for folder in (first, second, other):
        folder.mkdir()
    _link(config_dir, _local_link(first))
    _link(config_dir, _local_link(second, "cloud-2"))
    # Linked to the purged project's cloud project, but from another folder.
    _link(config_dir, _local_link(other))
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _hidden(
                    "lp_first",
                    first,
                    cloud={"project_id": "cloud-1", "state": "backed"},
                ),
                _hidden("lp_local", root / "local"),
                _hidden(
                    "lp_second",
                    second,
                    cloud={"project_id": "cloud-2", "state": "backed"},
                ),
            ],
        },
    )

    projects = await _list()

    assert [(p.root_paths, p.cloud) for p in projects] == [
        ([str(other)], _backed("cloud-1"))
    ]
    assert [entry["id"] for entry in _read_store(config_dir)["projects"]] == [
        projects[0].id
    ]
    assert _linked_folders(config_dir) == {other}


@pytest.mark.asyncio
async def test_purging_keeps_a_link_a_kept_project_on_the_same_folder_uses(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    backed = {"project_id": "cloud-1", "state": "backed"}
    _link(config_dir, _local_link(root))
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _hidden("lp_old", root, cloud=backed),
                _entry("lp_kept", root, cloud=backed),
            ],
        },
    )

    await _list()

    assert [entry["id"] for entry in _read_store(config_dir)["projects"]] == ["lp_kept"]
    assert _linked_folders(config_dir) == {root}


@pytest.mark.asyncio
async def test_purging_a_retired_project_unlinks_its_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    retired = {"project_id": "cloud-gone", "state": "retired"}
    _link(config_dir, _local_link(root, "cloud-gone"))
    _write_store(
        config_dir, {"version": 1, "projects": [_hidden("lp_1", root, cloud=retired)]}
    )

    assert await _list() == []
    assert _read_store(config_dir)["projects"] == []
    assert _linked_folders(config_dir) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("removed_by", ["remove", "older server"])
async def test_a_partly_read_links_file_still_drops_the_project_and_its_readable_links(
    config_dir: Path, tmp_path: Path, removed_by: str
) -> None:
    root = tmp_path.resolve()
    backed = {"project_id": "cloud-1", "state": "backed"}
    _link(config_dir, _local_link(root))
    links_file = config_dir / "projects.toml"
    with links_file.open("a", encoding="utf-8") as file:
        file.write('\n[[projects]]\nkind = "future"\n')
    entry = (
        _entry("lp_1", root, cloud=backed)
        if removed_by == "remove"
        else _hidden("lp_1", root, cloud=backed)
    )
    _write_store(config_dir, {"version": 1, "projects": [entry]})

    if removed_by == "remove":
        await _handler().dispatch("projects/remove", {"id": "lp_1"})

    assert await _list() == []
    assert _read_store(config_dir)["projects"] == []
    assert _linked_folders(config_dir) == set()
    assert 'kind = "future"' in links_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_set_cloud_backs_the_project_and_the_folder_link_keeps_it(
    config_dir: Path, tmp_path: Path
) -> None:
    created = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    backed = await _mutate(
        "projects/setCloud", {"id": created.id, "cloudProjectId": "cloud-1"}
    )
    _link(config_dir, _local_link(tmp_path, "cloud-1"))

    assert backed.id == created.id
    assert backed.cloud == _backed("cloud-1")
    assert [(project.id, project.cloud) for project in await _list()] == [
        (created.id, _backed("cloud-1"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("projects/rename", {"id": "lp_missing", "name": "x"}),
        ("projects/remove", {"id": "lp_missing"}),
        ("projects/setCloud", {"id": "lp_missing", "cloudProjectId": "cloud-1"}),
    ],
)
async def test_unknown_project_is_not_found(
    method: str, params: dict[str, Any]
) -> None:
    assert await _failure(method, params) is ProtocolErrorCode.NOT_FOUND


@pytest.mark.asyncio
async def test_a_project_an_older_server_hid_is_gone_even_when_the_purge_fails(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [_hidden("lp_hidden", root), _entry("lp_live", root)],
        },
    )

    def fail(*_: object) -> None:
        raise OSError("links unreadable")

    monkeypatch.setattr(LocalProjectStore, "purge_removed", fail)
    store = LocalProjectStore(_store_file(config_dir))
    stamped = PublicSession.model_construct(
        id="stamped",
        cwd=str(root),
        project=PublicSessionProject(kind="local", id="lp_hidden"),
    )

    assert [project.id for project in await _list()] == ["lp_live"]
    assert await _failure("projects/rename", {"id": "lp_hidden", "name": "x"}) is (
        ProtocolErrorCode.NOT_FOUND
    )
    assert _folder_stamps(store.list_projects(), store.project_ids(), [stamped]) == {
        "stamped": SessionProject(kind="local", id="lp_live")
    }


@pytest.mark.asyncio
async def test_invalid_entries_are_hidden_but_kept(
    config_dir: Path, tmp_path: Path
) -> None:
    broken = {"id": "lp_broken", "name": "no roots"}
    _write_store(config_dir, {"version": 1, "projects": [broken]})

    assert await _list() == []
    created = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    assert [project.id for project in await _list()] == [created.id]
    assert _read_store(config_dir)["projects"][0] == broken
    assert await _failure("projects/remove", {"id": "lp_broken"}) is (
        ProtocolErrorCode.NOT_FOUND
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not = [valid toml", b'x = "\xff"\n'])
async def test_an_unreadable_store_is_reported_and_never_overwritten(
    config_dir: Path, tmp_path: Path, content: bytes
) -> None:
    _store_file(config_dir).write_bytes(content)

    assert await _failure("projects/list", {}) is ProtocolErrorCode.INTERNAL_ERROR
    assert (
        await _failure("projects/create", {"rootPath": str(tmp_path)})
        is ProtocolErrorCode.INTERNAL_ERROR
    )
    assert _store_file(config_dir).read_bytes() == content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"version": 2, "projects": []},
        {"version": 1, "projects": {"lp_1": {"name": "table"}}},
        {"version": 1, "projects": ["lp_ref"]},
    ],
)
async def test_an_unsupported_store_is_never_overwritten(
    config_dir: Path, tmp_path: Path, data: dict[str, Any]
) -> None:
    _write_store(config_dir, data)
    before = _store_file(config_dir).read_text(encoding="utf-8")

    assert (
        await _failure("projects/create", {"rootPath": str(tmp_path)})
        is ProtocolErrorCode.INTERNAL_ERROR
    )
    assert _store_file(config_dir).read_text(encoding="utf-8") == before


@pytest.mark.asyncio
async def test_the_server_routes_project_calls_while_a_session_is_attached(
    tmp_path: Path,
) -> None:
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        oidc_tenants=fake_process_services().oidc_tenants,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
        host_handler=_handler(),
    )
    server._client_info = _CLIENT
    server._root = cast(Any, MagicMock(session_id="attached-session"))

    created = await server._dispatch_request(
        "projects/create", {"rootPath": str(tmp_path)}
    )
    listed = await server._dispatch_request("projects/list", {})

    project = cast(ProjectMutationResponse, created.response).project
    assert cast(ProjectsListResponse, listed.response).projects == [project]


@pytest.mark.asyncio
async def test_list_imports_project_links_as_backed_projects(
    config_dir: Path, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    folder = tmp_path / "folder"
    repo.mkdir()
    folder.mkdir()
    _link(
        config_dir,
        RemoteProjectLink(
            repo_root=repo,
            repo_url="https://github.com/mistralai/repo.git",
            project_id="cloud-repo",
            project_name="Repo",
        ),
    )
    _link(config_dir, _local_link(folder, "cloud-folder", "Folder"))

    projects = await _list()

    assert [(p.name, p.root_paths, p.cloud) for p in projects] == [
        ("Repo", [str(repo.resolve())], _backed("cloud-repo")),
        ("Folder", [str(folder.resolve())], _backed("cloud-folder")),
    ]
    assert all(str(UUID(p.id)) == p.id for p in projects)
    assert all(p.created_at == p.updated_at for p in projects)
    assert _read_store(config_dir)["projects"][0] == {
        "id": projects[0].id,
        "name": "Repo",
        "root_paths": [str(repo.resolve())],
        "created_at": projects[0].created_at,
        "updated_at": projects[0].updated_at,
        "cloud": {"project_id": "cloud-repo", "state": "backed"},
    }


@pytest.mark.asyncio
async def test_each_link_is_imported_once(config_dir: Path, tmp_path: Path) -> None:
    _link(config_dir, _local_link(tmp_path))
    first = await _list()
    before = _store_file(config_dir).read_bytes()

    assert await _list() == first
    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_links_made_later_are_imported_on_the_next_list(
    config_dir: Path, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    later = tmp_path / "later"
    first.mkdir()
    later.mkdir()
    _link(config_dir, _local_link(first, "cloud-1"))
    [existing] = await _list()

    _link(config_dir, _local_link(later, "cloud-2"))
    projects = await _list()

    assert projects[0] == existing
    assert [(p.root_paths, p.cloud) for p in projects[1:]] == [
        ([str(later.resolve())], _backed("cloud-2"))
    ]


@pytest.mark.asyncio
async def test_a_link_backs_the_first_unbacked_project_on_its_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_other", root / "elsewhere"),
                _entry("lp_mine", root, future="kept"),
                _entry("lp_second", root),
            ],
        },
    )
    _link(config_dir, _local_link(root))

    projects = await _list()

    assert [(p.id, p.cloud) for p in projects] == [
        ("lp_other", None),
        ("lp_mine", _backed("cloud-1")),
        ("lp_second", None),
    ]
    assert projects[1].name == "Linked"
    assert projects[1].updated_at > 1
    assert _read_store(config_dir)["projects"][1] == {
        **_entry("lp_mine", root, future="kept"),
        "name": "Linked",
        "cloud": {"project_id": "cloud-1", "state": "backed"},
        "updated_at": projects[1].updated_at,
    }


@pytest.mark.asyncio
async def test_a_link_rebacks_a_project_whose_backing_was_retired(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    retired = {"project_id": "cloud-old", "state": "retired"}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", root, cloud=retired)]}
    )
    _link(config_dir, _local_link(root, "cloud-new"))

    assert [(p.id, p.cloud) for p in await _list()] == [("lp_1", _backed("cloud-new"))]


@pytest.mark.asyncio
async def test_a_link_never_backs_a_project_holding_its_folder_as_a_secondary_root(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    multi = _entry("lp_multi", root / "primary")
    multi["root_paths"].append(str(root))
    _write_store(config_dir, {"version": 1, "projects": [multi]})
    _link(config_dir, _local_link(root))

    projects = await _list()

    assert [(p.id, p.root_paths, p.cloud) for p in projects] == [
        ("lp_multi", [str(root / "primary"), str(root)], None),
        (projects[1].id, [str(root)], _backed("cloud-1")),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("roots", "cloud"),
    [
        pytest.param([""], {"project_id": "cloud-1", "state": "backed"}, id="backed"),
        pytest.param([""], {"project_id": "cloud-1", "state": "retired"}, id="retired"),
        pytest.param(
            ["primary", ""],
            {"project_id": "cloud-1", "state": "backed"},
            id="secondary-root",
        ),
        pytest.param(
            [""], {"project_id": "cloud-old", "state": "deleting"}, id="unknown-state"
        ),
    ],
)
async def test_an_imported_folder_is_left_alone(
    config_dir: Path, tmp_path: Path, roots: list[str], cloud: dict[str, str]
) -> None:
    root = tmp_path.resolve()
    entry = _entry("lp_1", root, cloud=cloud)
    entry["root_paths"] = [str(root / name) if name else str(root) for name in roots]
    _write_store(config_dir, {"version": 1, "projects": [entry]})
    before = _store_file(config_dir).read_bytes()
    _link(config_dir, _local_link(root))

    await _list()

    assert _store_file(config_dir).read_bytes() == before
    assert not config_dir.joinpath(".local_projects.toml.lock").exists()


@pytest.mark.asyncio
async def test_unlinking_a_folder_keeps_its_project_without_the_backing(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root))
    [imported] = await _list()

    VibeProjectsStore(config_dir / "projects.toml").delete_project_link(repo_root=root)
    [unlinked] = await _list()
    _link(config_dir, _local_link(root))
    [relinked] = await _list()

    assert (unlinked.id, unlinked.cloud) == (imported.id, None)
    assert (relinked.id, relinked.cloud) == (imported.id, _backed("cloud-1"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        pytest.param("not = [valid", id="corrupt"),
        pytest.param("", id="mid-write"),
        pytest.param(
            'version = 1\n[[projects]]\nkind = "future"\n', id="unknown-entry"
        ),
    ],
)
async def test_a_partly_read_links_file_unlinks_nothing(
    config_dir: Path, tmp_path: Path, content: str
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root))
    [imported] = await _list()

    (config_dir / "projects.toml").write_text(content, encoding="utf-8")

    assert await _list() == [imported]


@pytest.mark.asyncio
async def test_relinking_a_folder_moves_its_backing(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root, "cloud-old"))
    [imported] = await _list()

    _link(config_dir, _local_link(root, "cloud-new"))

    assert [(p.id, p.cloud) for p in await _list()] == [
        (imported.id, _backed("cloud-new"))
    ]


@pytest.mark.asyncio
async def test_relinking_a_folder_takes_the_new_cloud_projects_name(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root, "cloud-old", "Old project"))
    [imported] = await _list()
    await _mutate("projects/rename", {"id": imported.id, "name": "Renamed"})

    _link(config_dir, _local_link(root, "cloud-new", "New project"))

    assert [(p.name, p.cloud) for p in await _list()] == [
        ("New project", _backed("cloud-new"))
    ]


@pytest.mark.asyncio
async def test_a_folder_linked_to_a_renamed_cloud_project_takes_its_local_name(
    config_dir: Path, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _link(config_dir, _local_link(first, name="Cloud name"))
    [imported] = await _list()
    await _mutate("projects/rename", {"id": imported.id, "name": "Renamed"})

    _link(config_dir, _local_link(second, name="Cloud name"))
    VibeProjectsStore(config_dir / "projects.toml").delete_project_link(
        repo_root=first.resolve()
    )

    assert [(p.name, p.root_paths, p.cloud) for p in await _list()] == [
        ("Renamed", [str(first.resolve())], None),
        ("Renamed", [str(second.resolve())], _backed("cloud-1")),
    ]


@pytest.mark.asyncio
async def test_a_backing_is_never_dropped_from_a_project_in_another_state(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    cloud = {"project_id": "cloud-1", "state": "creating"}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", root, cloud=cloud)]}
    )
    before = _store_file(config_dir).read_bytes()

    await _list()

    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_a_cloud_project_linked_to_two_folders_backs_one_project(
    config_dir: Path, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _link(config_dir, _local_link(first))
    _link(config_dir, _local_link(second))

    projects = await _list()

    assert [(p.root_paths, p.cloud) for p in projects] == [
        ([str(first.resolve()), str(second.resolve())], _backed("cloud-1"))
    ]


@pytest.mark.asyncio
async def test_a_folder_linked_later_joins_the_backed_project_as_a_context_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    first.mkdir()
    second.mkdir()
    _link(config_dir, _local_link(first))
    [imported] = await _list()
    local = await _mutate("projects/create", {"rootPath": str(second)})

    _link(config_dir, _local_link(second))
    projects = await _list()

    # The folder's own local project joins it too.
    assert [(p.id, p.name, p.root_paths, p.cloud) for p in projects] == [
        (imported.id, "Linked", [str(first), str(second)], _backed("cloud-1"))
    ]
    assert local.id not in {
        entry["id"] for entry in _read_store(config_dir)["projects"]
    }


@pytest.mark.asyncio
async def test_relinking_a_context_folder_moves_it_to_its_new_cloud_project(
    config_dir: Path, tmp_path: Path
) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    first.mkdir()
    second.mkdir()
    _link(config_dir, _local_link(first, "cloud-1"))
    _link(config_dir, _local_link(second, "cloud-1"))
    await _list()

    _link(config_dir, _local_link(second, "cloud-2", "Two"))

    assert [(p.root_paths, p.cloud) for p in await _list()] == [
        ([str(first)], _backed("cloud-1")),
        ([str(second)], _backed("cloud-2")),
    ]


@pytest.mark.asyncio
async def test_a_relinked_context_folder_leaves_a_retired_project(
    config_dir: Path, tmp_path: Path
) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    first.mkdir()
    second.mkdir()
    retired = _entry("lp_1", first, cloud={"project_id": "cloud-1", "state": "retired"})
    retired["root_paths"].append(str(second))
    _write_store(config_dir, {"version": 1, "projects": [retired]})
    _link(config_dir, _local_link(first, "cloud-1"))
    _link(config_dir, _local_link(second, "cloud-2", "Two"))

    assert [(p.root_paths, p.cloud) for p in await _list()] == [
        ([str(first)], LocalProjectCloudView(project_id="cloud-1", state="retired")),
        ([str(second)], _backed("cloud-2")),
    ]


@pytest.mark.asyncio
async def test_relinking_a_folder_to_a_backed_cloud_project_merges_its_project(
    config_dir: Path, tmp_path: Path
) -> None:
    first = (tmp_path / "first").resolve()
    second = (tmp_path / "second").resolve()
    first.mkdir()
    second.mkdir()
    _link(config_dir, _local_link(first, "cloud-1"))
    _link(config_dir, _local_link(second, "cloud-2"))
    [kept, _] = await _list()

    _link(config_dir, _local_link(second, "cloud-1"))

    assert [(p.id, p.root_paths, p.cloud) for p in await _list()] == [
        (kept.id, [str(first), str(second)], _backed("cloud-1"))
    ]


@pytest.mark.asyncio
async def test_a_store_backing_a_cloud_project_twice_is_merged_into_its_first_project(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    backed = {"project_id": "cloud-1", "state": "backed"}
    second = _entry("lp_second", root / "b", cloud=backed)
    second["root_paths"].append(str(root / "a"))
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_first", root / "a", cloud=backed),
                _entry("lp_local", root / "c"),
                second,
            ],
        },
    )
    before = _store_file(config_dir).read_bytes()

    listed = LocalProjectStore(_store_file(config_dir)).list_projects()

    assert [(p.id, p.root_paths) for p in listed] == [
        ("lp_first", (root / "a", root / "b")),
        ("lp_local", (root / "c",)),
    ]
    assert _store_file(config_dir).read_bytes() == before
    await _list()
    assert [entry["id"] for entry in _read_store(config_dir)["projects"]] == [
        "lp_first",
        "lp_local",
    ]


def test_a_worktree_session_of_a_merged_project_follows_it(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    backed = {"project_id": "cloud-1", "state": "backed"}
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_first", root / "a", cloud=backed),
                _entry("lp_second", root / "b", cloud=backed),
            ],
        },
    )
    store = LocalProjectStore(_store_file(config_dir))
    # A worktree matches no project folder: only its stamp names its project.
    session = PublicSession.model_construct(
        id="session-1",
        cwd=str(WORKTREES_DIR.path / "b-feature"),
        project=PublicSessionProject(kind="local", id="lp_second"),
    )

    stamps = _folder_stamps(store.list_projects(), store.project_ids(), [session])

    assert stamps == {"session-1": SessionProject(kind="local", id="lp_first")}


@pytest.mark.asyncio
async def test_an_imported_link_without_a_name_is_named_after_its_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "widgets"
    root.mkdir()
    _link(config_dir, _local_link(root, name=" "))

    [project] = await _list()

    assert project.name == "widgets"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("folder", "cloud_id"),
    [
        pytest.param("missing", "cloud-1", id="missing-folder"),
        pytest.param("", "cloud-1", id="disk-root"),
        pytest.param(".", " ", id="blank-cloud-id"),
    ],
)
async def test_unusable_links_are_not_imported(
    config_dir: Path, tmp_path: Path, folder: str, cloud_id: str
) -> None:
    root = tmp_path / folder if folder else Path(tmp_path.anchor)
    _link(config_dir, _local_link(root, cloud_id))

    assert await _list() == []
    assert not _store_file(config_dir).exists()


@pytest.mark.skipif(
    os.name == "nt" or os.geteuid() == 0, reason="POSIX permissions, not as root"
)
@pytest.mark.asyncio
async def test_an_unreadable_link_folder_does_not_block_the_other_links(
    config_dir: Path, tmp_path: Path
) -> None:
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    folder = tmp_path / "folder"
    folder.mkdir()
    _link(config_dir, _local_link(locked / "inner", "cloud-locked"))
    _link(config_dir, _local_link(folder, "cloud-folder"))
    locked.chmod(0)
    try:
        projects = await _list()
    finally:
        locked.chmod(0o700)

    assert [(p.root_paths, p.cloud) for p in projects] == [
        ([str(folder.resolve())], _backed("cloud-folder"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [b"not = [valid toml", b'x = "\xff"\n'])
async def test_links_never_overwrite_an_unreadable_store(
    config_dir: Path, tmp_path: Path, content: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store_file(config_dir).write_bytes(content)
    _link(config_dir, _local_link(tmp_path))
    captured: list[str] = []
    monkeypatch.setattr(
        local_projects_controller_module,
        "capture_sentry_exception",
        lambda exc, **_: captured.append(str(exc)),
    )

    assert await _failure("projects/list", {}) is ProtocolErrorCode.INTERNAL_ERROR
    assert _store_file(config_dir).read_bytes() == content
    assert captured == []


@pytest.mark.asyncio
async def test_links_never_overwrite_an_unsupported_store(
    config_dir: Path, tmp_path: Path
) -> None:
    _write_store(config_dir, {"version": 2, "projects": []})
    before = _store_file(config_dir).read_bytes()
    _link(config_dir, _local_link(tmp_path))

    assert await _list() == []
    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_a_link_another_server_imported_first_is_not_imported_again(
    config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root))
    theirs = _entry(
        "lp_theirs", root, cloud={"project_id": "cloud-1", "state": "backed"}
    )
    real_lock = local_projects_module.exclusive_file_lock

    @contextmanager
    def lock_after_their_import(path: Path) -> Iterator[None]:
        _write_store(config_dir, {"version": 1, "projects": [theirs]})
        with real_lock(path):
            yield

    monkeypatch.setattr(
        local_projects_module, "exclusive_file_lock", lock_after_their_import
    )

    assert [p.id for p in await _list()] == ["lp_theirs"]
    assert _read_store(config_dir)["projects"] == [theirs]


@pytest.mark.asyncio
async def test_list_still_answers_when_links_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    def symlink_loop(_: VibeProjectsStore) -> tuple[list[ProjectLink], bool]:
        raise RuntimeError("Symlink loop")

    captured: list[str] = []
    monkeypatch.setattr(VibeProjectsStore, "read_project_links", symlink_loop)
    monkeypatch.setattr(
        local_projects_controller_module,
        "capture_sentry_exception",
        lambda exc, **_: captured.append(str(exc)),
    )

    assert await _list() == [created]
    assert captured == ["Symlink loop"]


def test_set_cloud_replaces_the_backing(config_dir: Path, tmp_path: Path) -> None:
    cloud = {"project_id": "cloud-1", "state": "retired", "retired_at": 5}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=cloud)]}
    )
    store = LocalProjectStore(_store_file(config_dir))

    project = store.set_cloud(
        "lp_1", CloudBacking(project_id="cloud-2", state="requested")
    )

    assert project.cloud == CloudBacking(project_id="cloud-2", state="requested")
    assert project.updated_at > 1
    assert _read_store(config_dir)["projects"][0]["cloud"] == {
        "project_id": "cloud-2",
        "state": "requested",
    }


def test_set_cloud_never_backs_a_cloud_project_twice(
    config_dir: Path, tmp_path: Path
) -> None:
    backed = {"project_id": "cloud-1", "state": "backed"}
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry("lp_1", tmp_path / "a", cloud=backed),
                _entry("lp_2", tmp_path / "b"),
            ],
        },
    )
    before = _store_file(config_dir).read_bytes()
    store = LocalProjectStore(_store_file(config_dir))

    with pytest.raises(LocalProjectError):
        store.set_cloud("lp_2", CloudBacking(project_id="cloud-1", state="backed"))
    assert _store_file(config_dir).read_bytes() == before


_OWNER = CloudOwner(organization_id="org-a", workspace_id="ws-a", user_id="user-a")
_OTHER_OWNER = CloudOwner(
    organization_id="org-b", workspace_id="ws-b", user_id="user-a"
)
_STORED_OWNER = {
    "organization_id": "org-a",
    "workspace_id": "ws-a",
    "user_id": "user-a",
}


@pytest.mark.asyncio
async def test_set_cloud_keeps_the_account_that_made_the_backing(
    config_dir: Path, tmp_path: Path
) -> None:
    created = await _mutate("projects/create", {"rootPath": str(tmp_path)})
    owner = {"organizationId": "org-a", "workspaceId": "ws-a", "userId": "user-a"}

    backed = await _mutate(
        "projects/setCloud",
        {"id": created.id, "cloudProjectId": "cloud-1", "owner": owner},
    )
    _link(config_dir, _local_link(tmp_path, "cloud-1"))

    expected = LocalProjectCloudView(
        project_id="cloud-1",
        state="backed",
        owner=LocalProjectCloudOwner(
            organization_id="org-a", workspace_id="ws-a", user_id="user-a"
        ),
    )
    assert backed.cloud == expected
    assert [project.cloud for project in await _list()] == [expected]
    assert _read_store(config_dir)["projects"][0]["cloud"] == {
        "project_id": "cloud-1",
        "state": "backed",
        "owner": _STORED_OWNER,
    }


def test_set_cloud_gives_a_backing_made_before_owners_its_account(
    config_dir: Path, tmp_path: Path
) -> None:
    backed = {"project_id": "cloud-1", "state": "backed"}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=backed)]}
    )
    store = LocalProjectStore(_store_file(config_dir))

    project = store.set_cloud(
        "lp_1", CloudBacking(project_id="cloud-1", state="backed", owner=_OWNER)
    )

    assert project.cloud == CloudBacking(
        project_id="cloud-1", state="backed", owner=_OWNER
    )


def test_set_cloud_without_an_account_keeps_the_backings_account(
    config_dir: Path, tmp_path: Path
) -> None:
    backed = {"project_id": "cloud-1", "state": "backed", "owner": _STORED_OWNER}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=backed)]}
    )
    store = LocalProjectStore(_store_file(config_dir))

    project = store.set_cloud(
        "lp_1", CloudBacking(project_id="cloud-1", state="backed")
    )

    assert project.cloud == CloudBacking(
        project_id="cloud-1", state="backed", owner=_OWNER
    )


@pytest.mark.parametrize(
    "cloud",
    [
        CloudBacking(project_id="cloud-2", state="backed", owner=_OWNER),
        CloudBacking(project_id="cloud-1", state="backed", owner=_OTHER_OWNER),
    ],
    ids=["another cloud project", "another account"],
)
def test_set_cloud_never_takes_a_backed_project_from_its_cloud_project(
    config_dir: Path, tmp_path: Path, cloud: CloudBacking
) -> None:
    backed = {"project_id": "cloud-1", "state": "backed", "owner": _STORED_OWNER}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=backed)]}
    )
    before = _store_file(config_dir).read_bytes()
    store = LocalProjectStore(_store_file(config_dir))

    with pytest.raises(LocalProjectError):
        store.set_cloud("lp_1", cloud)
    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_retire_cloud_keeps_the_backings_account(
    config_dir: Path, tmp_path: Path
) -> None:
    backed = {"project_id": "cloud-gone", "state": "backed", "owner": _STORED_OWNER}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=backed)]}
    )

    await _handler().dispatch("projects/retireCloud", {"cloudProjectId": "cloud-gone"})

    [project] = LocalProjectStore(_store_file(config_dir)).list_projects()
    assert project.cloud == CloudBacking(
        project_id="cloud-gone", state="retired", owner=_OWNER
    )


def test_set_cloud_on_an_unknown_project_is_not_found(config_dir: Path) -> None:
    store = LocalProjectStore(_store_file(config_dir))

    with pytest.raises(LocalProjectNotFound):
        store.set_cloud("lp_missing", CloudBacking(project_id="c", state="backed"))
    assert not _store_file(config_dir).exists()


def _link_params(root: Path, cloud_id: str = "cloud-1") -> dict[str, Any]:
    return {"rootPath": str(root), "cloudProjectId": cloud_id, "name": "Team project"}


@pytest.mark.asyncio
async def test_link_keeps_the_account_that_links_the_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "widgets"
    root.mkdir()
    owner = {"organizationId": "org-a", "workspaceId": "ws-a", "userId": "user-a"}

    linked = await _mutate("projects/link", {**_link_params(root), "owner": owner})

    assert linked.cloud == LocalProjectCloudView(
        project_id="cloud-1",
        state="backed",
        owner=LocalProjectCloudOwner(
            organization_id="org-a", workspace_id="ws-a", user_id="user-a"
        ),
    )
    assert _read_store(config_dir)["projects"][0]["cloud"]["owner"] == _STORED_OWNER


@pytest.mark.asyncio
@pytest.mark.parametrize("cloud_id", [None, "cloud-1", "cloud-other"])
async def test_link_purges_a_project_an_older_server_hid_on_the_folder(
    config_dir: Path, tmp_path: Path, cloud_id: str | None
) -> None:
    root = tmp_path.resolve()
    cloud = (
        {}
        if cloud_id is None
        else {"cloud": {"project_id": cloud_id, "state": "backed"}}
    )
    _write_store(
        config_dir, {"version": 1, "projects": [_hidden("lp_removed", root, **cloud)]}
    )

    linked = await _mutate("projects/link", _link_params(root))

    assert linked.id != "lp_removed"
    assert (linked.root_paths, linked.cloud) == ([str(root)], _backed("cloud-1"))
    assert [project.id for project in await _list()] == [linked.id]


@pytest.mark.asyncio
async def test_link_creates_a_backed_project_for_a_new_folder(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path / "widgets"
    root.mkdir()

    linked = await _mutate("projects/link", _link_params(root))

    assert str(UUID(linked.id)) == linked.id
    assert (linked.name, linked.root_paths, linked.cloud) == (
        "Team project",
        [str(root.resolve())],
        _backed("cloud-1"),
    )
    assert _read_store(config_dir)["projects"] == [
        {
            "id": linked.id,
            "name": "Team project",
            "root_paths": [str(root.resolve())],
            "created_at": linked.created_at,
            "updated_at": linked.updated_at,
            "cloud": {"project_id": "cloud-1", "state": "backed"},
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cloud",
    [
        pytest.param(None, id="local-only"),
        pytest.param({"project_id": "cloud-old", "state": "retired"}, id="retired"),
    ],
)
async def test_link_backs_the_unbacked_project_on_the_folder(
    config_dir: Path, tmp_path: Path, cloud: dict[str, str] | None
) -> None:
    root = tmp_path.resolve()
    extra: dict[str, Any] = {"future": "kept"} if cloud is None else {"cloud": cloud}
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                _entry(
                    "lp_secondary",
                    root / "primary",
                    root_paths=[str(root / "primary"), str(root)],
                ),
                _entry("lp_mine", root, **extra),
            ],
        },
    )

    linked = await _mutate("projects/link", _link_params(root))

    assert (linked.id, linked.name, linked.cloud) == (
        "lp_mine",
        "Team project",
        _backed("cloud-1"),
    )
    assert linked.updated_at > 1
    stored = _read_store(config_dir)["projects"]
    assert [(entry["id"], entry.get("cloud")) for entry in stored] == [
        ("lp_secondary", None),
        ("lp_mine", {"project_id": "cloud-1", "state": "backed"}),
    ]
    if cloud is None:
        assert stored[1]["future"] == "kept"


@pytest.mark.asyncio
async def test_link_keeps_a_folder_already_backed_by_the_cloud_project(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    cloud = {"project_id": "cloud-1", "state": "backed"}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", root, cloud=cloud)]}
    )
    _link(config_dir, _local_link(root, "cloud-1"))
    before = _store_file(config_dir).read_bytes()

    linked = await _mutate("projects/link", _link_params(root))

    assert (linked.id, linked.name, linked.cloud) == (
        "lp_1",
        root.name,
        _backed("cloud-1"),
    )
    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_link_refuses_a_folder_another_cloud_project_backs(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    cloud = {"project_id": "cloud-other", "state": "backed"}
    _write_store(
        config_dir, {"version": 1, "projects": [_entry("lp_1", root, cloud=cloud)]}
    )
    _link(config_dir, _local_link(root, "cloud-other"))
    before = _store_file(config_dir).read_bytes()

    code = await _failure("projects/link", _link_params(root))

    assert code is ProtocolErrorCode.CONFLICT
    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_link_refuses_a_folder_linked_to_another_cloud_project_not_listed_yet(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    _link(config_dir, _local_link(root, "cloud-other"))

    code = await _failure("projects/link", _link_params(root))

    assert code is ProtocolErrorCode.CONFLICT
    assert [(p.root_paths, p.cloud) for p in await _list()] == [
        ([str(root)], _backed("cloud-other"))
    ]


@pytest.mark.asyncio
async def test_link_adds_a_second_folder_to_the_cloud_projects_local_project(
    config_dir: Path, tmp_path: Path
) -> None:
    first = tmp_path.resolve() / "first"
    second = tmp_path.resolve() / "second"
    first.mkdir()
    second.mkdir()
    _write_store(config_dir, {"version": 1, "projects": [_entry("lp_2", second)]})
    existing = await _mutate("projects/link", _link_params(first))
    _link(config_dir, _local_link(first, "cloud-1", "Team project"))
    await _mutate("projects/rename", {"id": existing.id, "name": "Renamed"})

    linked = await _mutate("projects/link", _link_params(second))

    assert (linked.id, linked.name, linked.root_paths, linked.cloud) == (
        existing.id,
        "Renamed",
        [str(first), str(second)],
        _backed("cloud-1"),
    )
    # The folder's own project merges in, so its sessions follow.
    [stored] = _read_store(config_dir)["projects"]
    assert stored["merged_ids"] == ["lp_2"]


@pytest.mark.asyncio
async def test_a_linked_project_is_kept_once_its_folder_link_is_saved(
    config_dir: Path, tmp_path: Path
) -> None:
    root = tmp_path.resolve()
    linked = await _mutate("projects/link", _link_params(root))

    _link(config_dir, _local_link(root, "cloud-1", "Team project"))

    assert await _list() == [linked]


@pytest.mark.asyncio
async def test_link_rejects_a_path_that_is_not_a_folder(config_dir: Path) -> None:
    code = await _failure("projects/link", _link_params(Path("/no/such/folder")))

    assert code is ProtocolErrorCode.INVALID_PARAMS
    assert not _store_file(config_dir).exists()


@pytest.mark.asyncio
async def test_retire_cloud_retires_the_backing_by_the_gone_cloud_project(
    config_dir: Path, tmp_path: Path
) -> None:
    gone = {"project_id": "cloud-gone", "state": "backed"}
    other = {"project_id": "cloud-other", "state": "backed"}
    first = _entry("lp_1", tmp_path / "a", cloud=gone, archived_at=5)
    first["root_paths"].append(str(tmp_path / "b"))
    _write_store(
        config_dir,
        {
            "version": 1,
            "projects": [
                first,
                _entry("lp_3", tmp_path / "c", cloud=other),
                _entry("lp_4", tmp_path / "d"),
            ],
        },
    )

    await _handler().dispatch("projects/retireCloud", {"cloudProjectId": "cloud-gone"})

    retired = CloudBacking(project_id="cloud-gone", state="retired")
    projects = LocalProjectStore(_store_file(config_dir)).list_projects()
    # The context folder stays with the project.
    assert [(p.id, p.root_paths, p.cloud) for p in projects] == [
        ("lp_1", (tmp_path / "a", tmp_path / "b"), retired),
        (
            "lp_3",
            (tmp_path / "c",),
            CloudBacking(project_id="cloud-other", state="backed"),
        ),
        ("lp_4", (tmp_path / "d",), None),
    ]
    stored = _read_store(config_dir)["projects"][0]
    assert stored["archived_at"] == 5
    assert stored["updated_at"] > 1


@pytest.mark.asyncio
async def test_retiring_a_cloud_project_nothing_backs_writes_nothing(
    config_dir: Path, tmp_path: Path
) -> None:
    retired = {"project_id": "cloud-gone", "state": "retired"}
    _write_store(
        config_dir,
        {"version": 1, "projects": [_entry("lp_1", tmp_path, cloud=retired)]},
    )
    before = _store_file(config_dir).read_bytes()

    await _handler().dispatch("projects/retireCloud", {"cloudProjectId": "cloud-gone"})

    assert _store_file(config_dir).read_bytes() == before


@pytest.mark.asyncio
async def test_a_retired_backing_is_not_imported_again_from_its_link(
    config_dir: Path, tmp_path: Path
) -> None:
    _link(config_dir, _local_link(tmp_path, "cloud-gone"))
    [imported] = await _list()

    await _handler().dispatch("projects/retireCloud", {"cloudProjectId": "cloud-gone"})

    assert [(p.id, p.cloud) for p in await _list()] == [
        (imported.id, LocalProjectCloudView(project_id="cloud-gone", state="retired"))
    ]


@pytest.mark.asyncio
async def test_a_folder_linked_to_a_retired_cloud_project_is_not_imported(
    config_dir: Path, tmp_path: Path
) -> None:
    first = tmp_path / "first"
    later = tmp_path / "later"
    first.mkdir()
    _link(config_dir, _local_link(first, "cloud-gone"))
    _link(config_dir, _local_link(later, "cloud-gone"))
    await _list()
    await _handler().dispatch("projects/retireCloud", {"cloudProjectId": "cloud-gone"})
    later.mkdir()

    projects = await _list()

    assert [(p.root_paths, p.cloud) for p in projects] == [
        (
            [str(first.resolve())],
            LocalProjectCloudView(project_id="cloud-gone", state="retired"),
        )
    ]


def test_session_project_resolves_ids_like_the_runner(tmp_path: Path) -> None:
    def project(project_id: str, cloud: CloudBacking | None) -> LocalProject:
        return LocalProject(
            id=project_id,
            name=project_id,
            root_paths=(tmp_path,),
            created_at=1,
            updated_at=1,
            cloud=cloud,
        )

    projects = [
        project("lp_backed", CloudBacking(project_id="cloud-1", state="backed")),
        project("lp_requested", CloudBacking(project_id="cloud-2", state="requested")),
    ]

    assert session_project(projects, "cloud-1") == SessionProject(
        kind="local", id="lp_backed"
    )
    assert session_project(projects, "lp_backed") == SessionProject(
        kind="local", id="lp_backed"
    )
    assert session_project(projects, "lp_requested") == SessionProject(
        kind="local", id="lp_requested"
    )
    # Not backed yet, the project does not go by its cloud id.
    assert session_project(projects, "cloud-2") == SessionProject(
        kind="cloud", id="cloud-2"
    )
