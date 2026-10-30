from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tomllib
from typing import Any, cast
from unittest.mock import MagicMock

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
    LocalProjectNotFound,
    LocalProjectStore,
)
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server.protocol import (
    SERVER_METHODS,
    LocalProjectCloudView,
    LocalProjectView,
    ProjectMutationResponse,
    ProjectsListResponse,
    ProtocolErrorCode,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.vibe_code_project import (
    LocalProjectLink,
    ProjectLink,
    RemoteProjectLink,
    VibeProjectsStore,
)

_CLIENT = ClientInfo(name="test-client", version="0.0.0")


def _handler() -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user",)),
        builtin_plugin_roots=[],
        org_config=fake_process_services().org_config,
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


def _link(config_dir: Path, link: ProjectLink) -> None:
    VibeProjectsStore(config_dir / "projects.toml").upsert_project_link(link)


def _local_link(
    root: Path, cloud_id: str = "cloud-1", name: str = "Linked"
) -> ProjectLink:
    return LocalProjectLink(directory_path=root, project_id=cloud_id, project_name=name)


def _backed(cloud_id: str) -> LocalProjectCloudView:
    return LocalProjectCloudView(project_id=cloud_id, state="backed")


def test_project_methods_are_advertised() -> None:
    assert {
        "projects/create",
        "projects/list",
        "projects/remove",
        "projects/rename",
    }.issubset(SERVER_METHODS)


@pytest.mark.asyncio
async def test_config_read_reports_the_local_projects_flag_set_in_the_config(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    config_file.write_text(
        "experimental_enable_local_projects = true\n"
        + config_file.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = await _handler().dispatch("config/read", {}, client_info=_CLIENT)

    payload = result.response.model_dump(mode="json", by_alias=True)
    assert payload["config"]["experimentalEnableLocalProjects"] is True


@pytest.mark.asyncio
async def test_config_read_reports_local_projects_off_by_default(
    config_dir: Path,
) -> None:
    result = await _handler().dispatch("config/read", {}, client_info=_CLIENT)

    payload = result.response.model_dump(mode="json", by_alias=True)
    assert payload["config"]["experimentalEnableLocalProjects"] is False


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

    assert created.id.startswith("lp_")
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
                _entry("lp_1", tmp_path, cloud=cloud, deleted_at=5),
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
        **_entry("lp_1", tmp_path, cloud=cloud, deleted_at=5),
        "name": "Renamed",
        "updated_at": renamed.updated_at,
    }
    assert stored[1] == _entry("lp_2", tmp_path)


@pytest.mark.asyncio
async def test_remove_drops_only_the_entry(config_dir: Path, tmp_path: Path) -> None:
    keep = await _mutate("projects/create", {"rootPath": str(tmp_path)})
    gone = await _mutate("projects/create", {"rootPath": str(tmp_path)})

    await _handler().dispatch("projects/remove", {"id": gone.id})

    assert await _list() == [keep]
    assert tmp_path.is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("projects/rename", {"id": "lp_missing", "name": "x"}),
        ("projects/remove", {"id": "lp_missing"}),
    ],
)
async def test_unknown_project_is_not_found(
    method: str, params: dict[str, Any]
) -> None:
    assert await _failure(method, params) is ProtocolErrorCode.NOT_FOUND


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
    assert all(p.id.startswith("lp_") for p in projects)
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
    assert projects[1].name == root.name
    assert projects[1].updated_at > 1
    assert _read_store(config_dir)["projects"][1] == {
        **_entry("lp_mine", root, future="kept"),
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
            [""], {"project_id": "cloud-old", "state": "backed"}, id="relinked"
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
async def test_a_cloud_project_linked_to_two_folders_backs_a_project_per_folder(
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
        ([str(first.resolve())], _backed("cloud-1")),
        ([str(second.resolve())], _backed("cloud-1")),
    ]


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

    def symlink_loop(_: VibeProjectsStore) -> list[ProjectLink]:
        raise RuntimeError("Symlink loop")

    captured: list[str] = []
    monkeypatch.setattr(VibeProjectsStore, "list_project_links", symlink_loop)
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


def test_set_cloud_on_an_unknown_project_is_not_found(config_dir: Path) -> None:
    store = LocalProjectStore(_store_file(config_dir))

    with pytest.raises(LocalProjectNotFound):
        store.set_cloud("lp_missing", CloudBacking(project_id="c", state="backed"))
    assert not _store_file(config_dir).exists()
