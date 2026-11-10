from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
import vibe.app_server._project_links as project_links
from vibe.app_server._project_links import (
    ProjectLinksController,
    ProjectLinksError,
    ProjectLinksInvalidRequest,
    _read_inspected_directory,
)
from vibe.app_server.protocol import SERVER_METHODS, ProtocolErrorCode
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.git.errors import GitError, GitRepositoryError
from vibe.core.vibe_code_project.selection import LocalProjectLink, RemoteProjectLink

REPO_URL = "https://github.com/acme/widgets.git"
REPO_ROOT = Path("/tmp/widgets")


class FutureGitRepositoryError(GitRepositoryError):
    pass


@pytest.fixture
def controller() -> ProjectLinksController:
    return ProjectLinksController()


def _inspected_directory(
    *, git: SimpleNamespace | None = None, path: Path = REPO_ROOT
) -> SimpleNamespace:
    return SimpleNamespace(
        path=path,
        name=path.name,
        git=git
        if git is not None
        else SimpleNamespace(
            github_repo_url=REPO_URL,
            current_branch="main",
            default_branch="main",
            has_commits=True,
        ),
    )


def test_git_inspection_failure_is_not_a_plain_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _raise(path: Path) -> None:
        raise GitError("git unavailable")

    monkeypatch.setattr(project_links, "GitRepo", SimpleNamespace(open=_raise))
    with pytest.raises(GitRepositoryError, match="git unavailable"):
        _read_inspected_directory(tmp_path)


class TestList:
    @pytest.mark.asyncio
    async def test_lists_metadata_without_api_key(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = RemoteProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id="proj-1",
            project_name="Widgets",
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(list_project_links=lambda: [link]),
        )
        assert await controller.list_links() == {
            "projects": [
                {
                    "projectId": "proj-1",
                    "localLinks": [
                        {"directoryPath": str(REPO_ROOT), "hasCommits": False}
                    ],
                }
            ]
        }

    @pytest.mark.asyncio
    async def test_groups_multiple_local_checkouts_by_project(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Multiple links on one project surface all their local checkout paths;
        # the loopback folds each onto the project's repositories.
        link_a = RemoteProjectLink(
            repo_root=Path("/a/widgets"),
            repo_url=REPO_URL,
            project_id="p1",
            project_name="Widgets",
        )
        link_b = RemoteProjectLink(
            repo_root=Path("/b/widgets"),
            repo_url=REPO_URL,
            project_id="p1",
            project_name="Widgets",
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(list_project_links=lambda: [link_a, link_b]),
        )
        result = await controller.list_links()
        assert result["projects"][0] == {
            "projectId": "p1",
            "localLinks": [
                {"directoryPath": "/a/widgets", "hasCommits": False},
                {"directoryPath": "/b/widgets", "hasCommits": False},
            ],
        }


class TestResolveRoot:
    @pytest.mark.asyncio
    async def test_eligible_root(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return _inspected_directory()

        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result["eligible"] is True
        assert result["root"] == {
            "directoryPath": str(REPO_ROOT),
            "directoryName": "widgets",
            "git": {
                "currentBranch": "main",
                "defaultBranch": "main",
                "githubRepoUrl": REPO_URL,
                "hasCommits": True,
            },
        }

    @pytest.mark.asyncio
    async def test_plain_directory_is_eligible(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return SimpleNamespace(path=REPO_ROOT, name="widgets", git=None)

        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result == {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "directoryPath": str(REPO_ROOT),
                "directoryName": "widgets",
                "git": None,
            },
        }

    @pytest.mark.asyncio
    async def test_nested_unresolvable(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(root_path: Path) -> SimpleNamespace:
            raise GitRepositoryError("some other failure")

        monkeypatch.setattr(controller, "_inspect_directory", _raise)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result["rejectReason"] == "nested_unresolvable"


class TestInspectRootWithoutGit:
    @pytest.fixture(autouse=True)
    def _no_git(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("GIT_PYTHON_GIT_EXECUTABLE", str(tmp_path / "no-git"))

    @pytest.mark.asyncio
    async def test_plain_directory_is_eligible(
        self, controller: ProjectLinksController, tmp_path: Path
    ) -> None:
        directory = tmp_path / "notes"
        directory.mkdir()

        result = await controller.inspect_root(str(directory))

        assert result["eligible"] is True
        assert result["rejectReason"] is None
        assert result["root"] == {
            "directoryPath": str(directory.resolve()),
            "directoryName": "notes",
            "git": None,
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker_is_file", [False, True])
    async def test_directory_inside_a_checkout_asks_for_git(
        self, controller: ProjectLinksController, tmp_path: Path, marker_is_file: bool
    ) -> None:
        checkout = tmp_path / "widgets"
        chosen = checkout / "src"
        chosen.mkdir(parents=True)
        if marker_is_file:
            (checkout / ".git").write_text("gitdir: /elsewhere/.git/worktrees/w\n")
        else:
            (checkout / ".git").mkdir()

        result = await controller.inspect_root(str(chosen))

        assert result["eligible"] is False
        assert result["rejectReason"] == "git_unavailable"
        assert result["root"] is None

    @pytest.mark.asyncio
    async def test_saving_a_checkout_is_an_invalid_request(
        self, controller: ProjectLinksController, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()

        with pytest.raises(ProjectLinksInvalidRequest):
            await controller.save(
                str(tmp_path),
                project_id="p1",
                project_name="widgets",
                expected_github_repo_url=None,
            )


class TestInspectRoot:
    @pytest.mark.asyncio
    async def test_keeps_local_link_when_directory_gains_github_remote(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return _inspected_directory()

        saved_link = LocalProjectLink(
            directory_path=REPO_ROOT,
            project_id="proj-local",
            project_name="Local Widgets",
        )
        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                get_project_link=lambda **kw: saved_link,
                delete_project_link=lambda **kw: pytest.fail(
                    "local links are not upgraded or deleted during inspection"
                ),
            ),
        )

        result = await controller.inspect_root(str(REPO_ROOT))

        assert result["savedLink"] == {
            "projectId": "proj-local",
            "projectName": "Local Widgets",
        }
        assert result["staleLinkCleared"] is False

    @pytest.mark.asyncio
    async def test_clears_stale_saved_link_when_remote_differs(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return _inspected_directory()

        stale_link = RemoteProjectLink(
            repo_root=REPO_ROOT,
            repo_url="https://github.com/acme/old-widgets.git",
            project_id="proj-old",
            project_name="Old Widgets",
        )
        deleted: list[Path] = []
        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                get_project_link=lambda **kw: stale_link,
                delete_project_link=lambda **kw: deleted.append(kw["repo_root"]),
            ),
        )

        result = await controller.inspect_root(str(REPO_ROOT))

        assert result["eligible"] is True
        assert result["savedLink"] is None
        assert result["staleLinkCleared"] is True
        assert deleted == [REPO_ROOT]

    @pytest.mark.asyncio
    async def test_stale_link_delete_failure_keeps_inspection_usable(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return _inspected_directory()

        stale_link = RemoteProjectLink(
            repo_root=REPO_ROOT,
            repo_url="https://github.com/acme/old-widgets.git",
            project_id="proj-old",
            project_name="Old Widgets",
        )
        captured: list[str] = []
        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        monkeypatch.setattr(
            project_links,
            "capture_sentry_exception",
            lambda exc, **kw: captured.append(str(exc)),
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                get_project_link=lambda **kw: stale_link,
                delete_project_link=lambda **kw: (_ for _ in ()).throw(
                    OSError("store is read-only")
                ),
            ),
        )

        result = await controller.inspect_root(str(REPO_ROOT))

        assert result["eligible"] is True
        assert result["savedLink"] is None
        assert result["staleLinkCleared"] is False
        assert result["staleLinkClearFailed"] is True
        assert captured == ["store is read-only"]

    @pytest.mark.asyncio
    async def test_git_repository_error_subclass_is_nested_unresolvable(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(root_path: Path) -> SimpleNamespace:
            raise FutureGitRepositoryError("future git metadata failure")

        monkeypatch.setattr(controller, "_inspect_directory", _raise)
        result = await controller.inspect_root(str(REPO_ROOT))
        assert result == {
            "eligible": False,
            "rejectReason": "nested_unresolvable",
            "root": None,
            "savedLink": None,
            "staleLinkCleared": False,
        }


class TestMutations:
    @pytest.mark.asyncio
    async def test_save_rejects_remote_change_before_persisting(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _inspect(root_path: Path) -> SimpleNamespace:
            return _inspected_directory()

        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        with pytest.raises(ProjectLinksInvalidRequest, match="remote changed"):
            await controller.save(
                str(REPO_ROOT),
                "proj-1",
                "Widgets",
                "https://github.com/acme/old-widgets.git",
            )

    @pytest.mark.asyncio
    async def test_save_rejects_remote_change_after_inspection(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inspections = iter([
            _inspected_directory(
                git=SimpleNamespace(
                    github_repo_url="https://github.com/acme/old-widgets.git",
                    current_branch="main",
                    default_branch="main",
                    has_commits=True,
                )
            ),
            _inspected_directory(),
        ])

        async def _inspect(root_path: Path) -> SimpleNamespace:
            return next(inspections)

        monkeypatch.setattr(controller, "_inspect_directory", _inspect)
        inspected = await controller.inspect_root(str(REPO_ROOT))
        assert (
            inspected["root"]["git"]["githubRepoUrl"]
            == "https://github.com/acme/old-widgets.git"
        )

        with pytest.raises(ProjectLinksInvalidRequest, match="remote changed"):
            await controller.save(
                str(REPO_ROOT),
                "proj-1",
                "Widgets",
                inspected["root"]["git"]["githubRepoUrl"],
            )

    @pytest.mark.asyncio
    async def test_unlink_deletes_local_store_without_api_key(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deleted: list[Path] = []
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                delete_project_link=lambda **kw: deleted.append(kw["repo_root"])
            ),
        )

        async def _resolve(root_path: str) -> SimpleNamespace:
            return _inspected_directory()

        monkeypatch.setattr(controller, "_resolve_inspection_root", _resolve)
        result = await controller.unlink(str(REPO_ROOT))
        assert result == {"unlinked": True}
        assert deleted == [REPO_ROOT]

    @pytest.mark.asyncio
    async def test_unlink_delete_failure_is_non_fatal(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[str] = []
        monkeypatch.setattr(
            project_links,
            "capture_sentry_exception",
            lambda exc, **kw: captured.append(str(exc)),
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                delete_project_link=lambda **kw: (_ for _ in ()).throw(
                    OSError("store is read-only")
                )
            ),
        )

        async def _resolve(root_path: str) -> SimpleNamespace:
            return _inspected_directory()

        monkeypatch.setattr(controller, "_resolve_inspection_root", _resolve)

        result = await controller.unlink(str(REPO_ROOT))

        assert result == {"unlinked": True}
        assert captured == ["store is read-only"]

    @pytest.mark.asyncio
    async def test_unlink_stale_root_clears_by_ancestor(
        self,
        controller: ProjectLinksController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        # A checkout that moved/was deleted no longer resolves as a git repo.
        # Links are keyed on the git-resolved root, which may be an ancestor of
        # the chosen path — unlink must still find and clear the stored link
        # (and must not report success while leaving it behind).
        root = (tmp_path / "widgets").resolve()
        chosen_subdir = root / "packages" / "app"
        deleted: list[Path] = []
        stored = RemoteProjectLink(
            repo_root=root, repo_url=REPO_URL, project_id="p1", project_name="P1"
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                list_project_links=lambda: [stored],
                delete_project_link=lambda **kw: deleted.append(kw["repo_root"]),
            ),
        )

        async def _raise(root_path: str) -> tuple[Path, SimpleNamespace]:
            raise ProjectLinksInvalidRequest("Not an eligible project root")

        monkeypatch.setattr(controller, "_resolve_inspection_root", _raise)
        result = await controller.unlink(str(chosen_subdir))
        assert result == {"unlinked": True}
        assert deleted == [root]

    @pytest.mark.asyncio
    async def test_unlink_stale_root_no_match_is_noop(
        self,
        controller: ProjectLinksController,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        deleted: list[Path] = []
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                list_project_links=lambda: [],
                delete_project_link=lambda **kw: deleted.append(kw["repo_root"]),
            ),
        )

        async def _raise(root_path: str) -> tuple[Path, SimpleNamespace]:
            raise ProjectLinksInvalidRequest("Not an eligible project root")

        monkeypatch.setattr(controller, "_resolve_inspection_root", _raise)
        result = await controller.unlink(str((tmp_path / "gone").resolve()))
        assert result == {"unlinked": True}
        assert deleted == []


class _FakeProjectLinksController:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_links(self) -> dict[str, Any]:
        self.calls.append(("list_links", ()))
        return {
            "projects": [
                {
                    "projectId": "p1",
                    "localLinks": [{"directoryPath": "/repo", "hasCommits": True}],
                }
            ]
        }

    async def resolve_root(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("resolve_root", (root_path,)))
        if self.error is not None:
            raise self.error
        return {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "directoryPath": "/repo",
                "directoryName": "widgets",
                "git": {
                    "currentBranch": "main",
                    "defaultBranch": "main",
                    "githubRepoUrl": "https://github.com/acme/widgets.git",
                    "hasCommits": True,
                },
            },
        }

    async def inspect_root(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("inspect_root", (root_path,)))
        return {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "directoryPath": "/repo",
                "directoryName": "widgets",
                "git": {
                    "currentBranch": "main",
                    "defaultBranch": "main",
                    "githubRepoUrl": "https://github.com/acme/widgets.git",
                    "hasCommits": True,
                },
            },
            "savedLink": {"projectId": "p1", "projectName": "Widgets"},
            "staleLinkCleared": False,
        }

    async def save(
        self,
        root_path: str,
        project_id: str,
        project_name: str,
        expected_github_repo_url: str | None,
    ) -> dict[str, Any]:
        self.calls.append((
            "save",
            (root_path, project_id, project_name, expected_github_repo_url),
        ))
        return {
            "link": {
                "projectId": project_id,
                "projectName": project_name,
                "directoryPath": root_path,
            }
        }

    async def unlink(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("unlink", (root_path,)))
        return {"unlinked": True}


class TestProjectLinksJsonRpc:
    def test_methods_are_advertised(self) -> None:
        assert {
            "projectLinks/inspectRoot",
            "projectLinks/list",
            "projectLinks/resolveRoot",
            "projectLinks/save",
            "projectLinks/unlink",
        }.issubset(SERVER_METHODS)

    @pytest.mark.parametrize(
        "method",
        [
            "projectLinks/create",
            "projectLinks/link",
            "projectLinks/picker/load",
            "projectLinks/picker/loadMore",
        ],
    )
    def test_cloud_project_methods_are_gone(self, method: str) -> None:
        assert method not in SERVER_METHODS

    @pytest.mark.asyncio
    async def test_dispatches_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(
            HarnessFilesManager(sources=("user",)),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        )
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/resolveRoot", {"rootPath": "/repo"}
        )

        assert controller.calls == [("resolve_root", ("/repo",))]
        assert result.response.model_dump(mode="json", by_alias=True) == {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "directoryPath": "/repo",
                "directoryName": "widgets",
                "git": {
                    "currentBranch": "main",
                    "defaultBranch": "main",
                    "githubRepoUrl": "https://github.com/acme/widgets.git",
                    "hasCommits": True,
                },
            },
        }

    @pytest.mark.asyncio
    async def test_dispatches_inspect_root_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(
            HarnessFilesManager(sources=("user",)),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        )
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/inspectRoot", {"rootPath": "/repo"}
        )

        assert controller.calls == [("inspect_root", ("/repo",))]
        assert (
            result.response.model_dump(mode="json", by_alias=True)["root"]["git"][
                "githubRepoUrl"
            ]
            == "https://github.com/acme/widgets.git"
        )

    @pytest.mark.asyncio
    async def test_dispatches_save_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(
            HarnessFilesManager(sources=("user",)),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        )
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/save",
            {
                "rootPath": "/repo",
                "projectId": "p1",
                "projectName": "Widgets",
                "expectedGithubRepoUrl": "https://github.com/acme/widgets.git",
            },
        )

        assert controller.calls == [
            ("save", ("/repo", "p1", "Widgets", "https://github.com/acme/widgets.git"))
        ]
        assert result.response.model_dump(mode="json", by_alias=True) == {
            "link": {
                "projectId": "p1",
                "projectName": "Widgets",
                "directoryPath": "/repo",
            }
        }

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (ProjectLinksInvalidRequest("bad root"), ProtocolErrorCode.INVALID_PARAMS),
            (ProjectLinksError("boom"), ProtocolErrorCode.INTERNAL_ERROR),
        ],
    )
    @pytest.mark.asyncio
    async def test_maps_controller_errors(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        code: ProtocolErrorCode,
        process_services: ProcessServices,
    ) -> None:
        handler = HostRequestHandler(
            HarnessFilesManager(sources=("user",)),
            org_config=process_services.org_config,
            worktrees=process_services.worktrees,
            experiments=process_services.experiments,
            model_catalog=process_services.model_catalog,
        )
        monkeypatch.setattr(
            handler, "_project_links", _FakeProjectLinksController(error)
        )

        with pytest.raises(RequestFailure) as excinfo:
            await handler.dispatch("projectLinks/resolveRoot", {"rootPath": "/repo"})

        assert excinfo.value.code is code
