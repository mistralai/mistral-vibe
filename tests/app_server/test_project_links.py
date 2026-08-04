from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
import vibe.app_server._project_links as project_links
from vibe.app_server._project_links import (
    ProjectLinksAuthError,
    ProjectLinksController,
    ProjectLinksInternalError,
    ProjectLinksInvalidRequest,
)
from vibe.app_server.protocol import SERVER_METHODS, ProtocolErrorCode
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.vibe_code_project import VibeCodeProjectApiError
from vibe.core.vibe_code_project.picker_service import (
    VibeCodeProjectCreateResult,
    VibeCodeProjectLoadMoreResult,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerState,
    VibeCodeProjectResolverError,
)
from vibe.core.vibe_code_project.selection import (
    ProjectPickerContext,
    ProjectRepository,
    VibeCodeProject,
    VibeCodeProjectLink,
)

REPO_URL = "https://github.com/acme/widgets.git"
REPO_ROOT = Path("/tmp/widgets")


class FutureServiceTeleportError(ServiceTeleportError):
    pass


@pytest.fixture
def controller() -> ProjectLinksController:
    return ProjectLinksController()


def _git_info(**overrides: Any) -> SimpleNamespace:
    base = {
        "repo_root": REPO_ROOT,
        "remote_url": REPO_URL,
        "repo": "widgets",
        "branch": "main",
        "default_branch": "main",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _project() -> VibeCodeProject:
    return VibeCodeProject(
        project_id="proj-1",
        name="Widgets",
        repositories=(ProjectRepository(repo_url=REPO_URL, default_branch="main"),),
        is_read_only=False,
    )


def _context(saved_link: VibeCodeProjectLink | None = None) -> ProjectPickerContext:
    return ProjectPickerContext(
        repo_root=REPO_ROOT,
        repo_url=REPO_URL,
        repo_name="widgets",
        saved_link=saved_link,
    )


class _FakeService:
    """Minimal stand-in for VibeCodeProjectPickerService."""

    def __init__(
        self,
        *,
        initial: VibeCodeProjectPickerInitialData | None = None,
        load_initial_error: Exception | None = None,
        link_error: Exception | None = None,
    ) -> None:
        self._initial = initial
        self._load_initial_error = load_initial_error
        self._link_error = link_error
        self.cleared = False

    async def find_linkable_project(
        self, *, project_id: str, repo_url: str
    ) -> VibeCodeProject:
        if self._link_error is not None:
            raise self._link_error
        return _project()

    async def load_initial(self, git_info: Any) -> VibeCodeProjectPickerInitialData:
        if self._load_initial_error is not None:
            raise self._load_initial_error
        assert self._initial is not None
        return self._initial

    async def load_more(self, state: Any) -> VibeCodeProjectLoadMoreResult:
        return VibeCodeProjectLoadMoreResult(
            state=VibeCodeProjectPickerState(
                projects=[_project()], next_cursor=None, repo_url=REPO_URL
            ),
            focus_project_id="proj-1",
        )

    async def create_project(self, **kwargs: Any) -> VibeCodeProjectCreateResult:
        return VibeCodeProjectCreateResult(
            state=VibeCodeProjectPickerState(
                projects=[], next_cursor=None, repo_url=REPO_URL
            ),
            project=_project(),
        )

    def save_project_link(
        self, *, context: Any, project_id: str, project_name: str
    ) -> VibeCodeProjectLink:
        return VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id=project_id,
            project_name=project_name,
        )

    def clear_project_link(self, context: Any) -> None:
        self.cleared = True


def _wire_service(
    controller: ProjectLinksController,
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeService,
) -> None:
    async def _resolve(root_path: str) -> tuple[Path, SimpleNamespace]:
        return REPO_ROOT, _git_info()

    async def _build(repo_root: Path) -> _FakeService:
        return service

    monkeypatch.setattr(controller, "_resolve_root", _resolve)
    monkeypatch.setattr(controller, "_build_service", _build)


class TestList:
    @pytest.mark.asyncio
    async def test_lists_metadata_without_api_key(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id="proj-1",
            project_name="Widgets",
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(list_remote_projects=lambda: [link]),
        )
        assert await controller.list_links() == {
            "projects": [{"projectId": "proj-1", "repoLocalPaths": [str(REPO_ROOT)]}]
        }

    @pytest.mark.asyncio
    async def test_groups_stored_links_by_project(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id="proj-1",
            project_name="Widgets",
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(list_remote_projects=lambda: [link]),
        )
        result = await controller.list_links()
        assert result["projects"][0] == {
            "projectId": "proj-1",
            "repoLocalPaths": [str(REPO_ROOT)],
        }

    @pytest.mark.asyncio
    async def test_groups_multiple_local_checkouts_by_project(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Multiple links on one project surface all their local checkout paths;
        # the loopback folds each onto the project's repositories.
        link_a = VibeCodeProjectLink(
            repo_root=Path("/a/widgets"),
            repo_url=REPO_URL,
            project_id="p1",
            project_name="Widgets",
        )
        link_b = VibeCodeProjectLink(
            repo_root=Path("/b/widgets"),
            repo_url=REPO_URL,
            project_id="p1",
            project_name="Widgets",
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(list_remote_projects=lambda: [link_a, link_b]),
        )
        result = await controller.list_links()
        assert result["projects"][0] == {
            "projectId": "p1",
            "repoLocalPaths": ["/a/widgets", "/b/widgets"],
        }


class TestResolveRoot:
    @pytest.mark.asyncio
    async def test_eligible_root(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info()

        monkeypatch.setattr(controller, "_git_info", _info)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result["eligible"] is True
        assert result["root"]["repoName"] == "widgets"
        assert result["root"]["repoLocalPath"] == str(REPO_ROOT)

    @pytest.mark.parametrize(
        ("message", "reason"),
        [
            ("Not a git repository", "not_git"),
            (
                "No GitHub remote found. Teleport only supports GitHub",
                "unsupported_remote",
            ),
            ("Could not determine current commit", "no_commits"),
        ],
    )
    @pytest.mark.asyncio
    async def test_reject_reasons(
        self,
        controller: ProjectLinksController,
        monkeypatch: pytest.MonkeyPatch,
        message: str,
        reason: str,
    ) -> None:
        async def _raise(root_path: Path) -> SimpleNamespace:
            raise ServiceTeleportNotSupportedError(message)

        monkeypatch.setattr(controller, "_git_info", _raise)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result == {"eligible": False, "rejectReason": reason, "root": None}

    @pytest.mark.asyncio
    async def test_nested_unresolvable(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(root_path: Path) -> SimpleNamespace:
            raise ServiceTeleportError("some other failure")

        monkeypatch.setattr(controller, "_git_info", _raise)
        result = await controller.resolve_root(str(REPO_ROOT))
        assert result["rejectReason"] == "nested_unresolvable"


class TestInspectRoot:
    @pytest.mark.asyncio
    async def test_no_remote_is_ineligible(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info(remote_url=None)

        monkeypatch.setattr(controller, "_git_info", _info)
        result = await controller.inspect_root(str(REPO_ROOT))
        assert result == {
            "eligible": False,
            "rejectReason": "unsupported_remote",
            "root": None,
            "savedLink": None,
            "staleLinkCleared": False,
        }

    @pytest.mark.asyncio
    async def test_clears_stale_saved_link_when_remote_differs(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info()

        stale_link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url="https://github.com/acme/old-widgets.git",
            project_id="proj-old",
            project_name="Old Widgets",
        )
        deleted: list[Path] = []
        monkeypatch.setattr(controller, "_git_info", _info)
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                get_remote_project=lambda **kw: stale_link,
                delete_remote_project=lambda **kw: deleted.append(kw["repo_root"]),
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
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info()

        stale_link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url="https://github.com/acme/old-widgets.git",
            project_id="proj-old",
            project_name="Old Widgets",
        )
        captured: list[str] = []
        monkeypatch.setattr(controller, "_git_info", _info)
        monkeypatch.setattr(
            project_links,
            "capture_sentry_exception",
            lambda exc, **kw: captured.append(str(exc)),
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                get_remote_project=lambda **kw: stale_link,
                delete_remote_project=lambda **kw: (_ for _ in ()).throw(
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
    async def test_service_teleport_subclass_is_nested_unresolvable(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _raise(root_path: Path) -> SimpleNamespace:
            raise FutureServiceTeleportError("future git metadata failure")

        monkeypatch.setattr(controller, "_git_info", _raise)
        result = await controller.inspect_root(str(REPO_ROOT))
        assert result == {
            "eligible": False,
            "rejectReason": "nested_unresolvable",
            "root": None,
            "savedLink": None,
            "staleLinkCleared": False,
        }


class TestPicker:
    @pytest.mark.asyncio
    async def test_load_returns_candidates(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        initial = VibeCodeProjectPickerInitialData(
            context=_context(),
            state=VibeCodeProjectPickerState(
                projects=[_project()], next_cursor=None, repo_url=REPO_URL
            ),
        )
        _wire_service(controller, monkeypatch, _FakeService(initial=initial))
        result = await controller.picker_load(str(REPO_ROOT))
        assert result["candidates"]["items"][0]["projectId"] == "proj-1"
        assert result["staleLinkCleared"] is False

    @pytest.mark.asyncio
    async def test_load_ranks_current_link_first(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        alpha = VibeCodeProject(
            project_id="proj-alpha",
            name="Alpha",
            repositories=(ProjectRepository(repo_url=REPO_URL),),
        )
        zeta = VibeCodeProject(
            project_id="proj-zeta",
            name="Zeta",
            repositories=(ProjectRepository(repo_url=REPO_URL),),
        )
        saved_link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id="proj-zeta",
            project_name="Zeta",
        )
        initial = VibeCodeProjectPickerInitialData(
            context=_context(saved_link=saved_link),
            state=VibeCodeProjectPickerState(
                projects=[alpha, zeta], next_cursor=None, repo_url=REPO_URL
            ),
        )
        _wire_service(controller, monkeypatch, _FakeService(initial=initial))
        result = await controller.picker_load(str(REPO_ROOT))
        items = result["candidates"]["items"]
        assert items[0]["projectId"] == "proj-zeta"
        assert items[0]["recommended"] is True
        assert result["savedLink"]["projectId"] == "proj-zeta"

    @pytest.mark.asyncio
    async def test_load_does_not_recommend_when_saved_link_is_off_page(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        other = VibeCodeProject(
            project_id="proj-other",
            name="Other",
            repositories=(ProjectRepository(repo_url=REPO_URL),),
        )
        saved_link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            project_id="proj-missing",
            project_name="Missing",
        )
        initial = VibeCodeProjectPickerInitialData(
            context=_context(saved_link=saved_link),
            state=VibeCodeProjectPickerState(
                projects=[other], next_cursor=None, repo_url=REPO_URL
            ),
        )
        _wire_service(controller, monkeypatch, _FakeService(initial=initial))
        result = await controller.picker_load(str(REPO_ROOT))
        items = result["candidates"]["items"]
        assert items[0]["projectId"] == "proj-other"
        assert all(item["recommended"] is False for item in items)
        assert result["savedLink"]["projectId"] == "proj-missing"

    @pytest.mark.asyncio
    async def test_load_clears_stale_saved_link(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale_link = VibeCodeProjectLink(
            repo_root=REPO_ROOT,
            repo_url="https://github.com/acme/old-widgets.git",
            project_id="proj-old",
            project_name="Old",
        )
        service = _FakeService(
            initial=VibeCodeProjectPickerInitialData(
                context=_context(saved_link=stale_link),
                state=VibeCodeProjectPickerState(
                    projects=[], next_cursor=None, repo_url=REPO_URL
                ),
            )
        )
        _wire_service(controller, monkeypatch, service)
        result = await controller.picker_load(str(REPO_ROOT))
        assert result["staleLinkCleared"] is True
        assert result["savedLink"] is None
        assert service.cleared is True

    @pytest.mark.asyncio
    async def test_load_maps_auth_error(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeService(
            load_initial_error=VibeCodeProjectApiError(
                "Vibe Code Web projects request failed (status 401): {}"
            )
        )
        _wire_service(controller, monkeypatch, service)
        with pytest.raises(ProjectLinksAuthError):
            await controller.picker_load(str(REPO_ROOT))

    @pytest.mark.asyncio
    async def test_non_auth_api_error_is_sanitized(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Non-auth VCW errors carry raw response text; neither Sentry nor the
        # returned error should include it.
        captured: list[str] = []
        monkeypatch.setattr(
            project_links,
            "capture_sentry_exception",
            lambda exc, **kw: captured.append(str(exc)),
        )
        secret = (
            "https://github.com/acme/secret (status 500): sensitive-diagnostic-body"
        )
        service = _FakeService(load_initial_error=VibeCodeProjectApiError(secret))
        _wire_service(controller, monkeypatch, service)
        with pytest.raises(ProjectLinksInternalError) as excinfo:
            await controller.picker_load(str(REPO_ROOT))
        assert "sensitive-diagnostic-body" not in str(excinfo.value)
        assert captured
        assert all("sensitive-diagnostic-body" not in entry for entry in captured)

    @pytest.mark.asyncio
    async def test_load_more_never_recommends(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_service(controller, monkeypatch, _FakeService())
        result = await controller.picker_load_more(str(REPO_ROOT), "next")
        items = result["candidates"]["items"]
        assert items
        assert all(item["recommended"] is False for item in items)


class TestMutations:
    @pytest.mark.asyncio
    async def test_create_and_link(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeService(
            initial=VibeCodeProjectPickerInitialData(
                context=_context(),
                state=VibeCodeProjectPickerState(
                    projects=[], next_cursor=None, repo_url=REPO_URL
                ),
            )
        )
        _wire_service(controller, monkeypatch, service)
        result = await controller.create(str(REPO_ROOT), "Widgets", "main")
        assert result["link"]["projectId"] == "proj-1"

    @pytest.mark.asyncio
    async def test_link_existing_project(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_service(controller, monkeypatch, _FakeService())
        result = await controller.link(str(REPO_ROOT), "proj-1", "Widgets")
        assert result["link"] == {
            "projectId": "proj-1",
            "projectName": "Widgets",
            "repoLocalPath": str(REPO_ROOT),
        }

    @pytest.mark.asyncio
    async def test_link_rejects_unavailable_project(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = _FakeService(
            link_error=VibeCodeProjectResolverError(
                "The selected Vibe Code project is not available for this repository"
            )
        )
        _wire_service(controller, monkeypatch, service)
        with pytest.raises(ProjectLinksInvalidRequest):
            await controller.link(str(REPO_ROOT), "proj-x", "X")

    @pytest.mark.asyncio
    async def test_link_uses_server_project_name(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _wire_service(controller, monkeypatch, _FakeService())
        result = await controller.link(str(REPO_ROOT), "proj-1", "Spoofed")
        assert result["link"]["projectName"] == "Widgets"

    @pytest.mark.asyncio
    async def test_save_rejects_remote_change_before_persisting(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info()

        monkeypatch.setattr(controller, "_git_info", _info)
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
        remotes = iter([
            _git_info(remote_url="https://github.com/acme/old-widgets.git"),
            _git_info(remote_url="https://github.com/acme/widgets.git"),
        ])

        async def _info(root_path: Path) -> SimpleNamespace:
            return next(remotes)

        monkeypatch.setattr(controller, "_git_info", _info)
        inspected = await controller.inspect_root(str(REPO_ROOT))
        assert inspected["root"]["repoUrl"] == "https://github.com/acme/old-widgets.git"

        with pytest.raises(ProjectLinksInvalidRequest, match="remote changed"):
            await controller.save(
                str(REPO_ROOT), "proj-1", "Widgets", inspected["root"]["repoUrl"]
            )

    @pytest.mark.asyncio
    async def test_save_rejects_empty_expected_remote(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _info(root_path: Path) -> SimpleNamespace:
            return _git_info()

        monkeypatch.setattr(controller, "_git_info", _info)
        with pytest.raises(ProjectLinksInvalidRequest, match="required"):
            await controller.save(str(REPO_ROOT), "proj-1", "Widgets", " ")

    @pytest.mark.asyncio
    async def test_unlink_deletes_local_store_without_api_key(
        self, controller: ProjectLinksController, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        deleted: list[Path] = []
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                delete_remote_project=lambda **kw: deleted.append(kw["repo_root"])
            ),
        )

        async def _resolve(root_path: str) -> tuple[Path, SimpleNamespace]:
            return REPO_ROOT, _git_info()

        async def _build(repo_root: Path) -> _FakeService:
            raise AssertionError("unlink must not require the project picker service")

        monkeypatch.setattr(controller, "_resolve_root", _resolve)
        monkeypatch.setattr(controller, "_build_service", _build)
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
                delete_remote_project=lambda **kw: (_ for _ in ()).throw(
                    OSError("store is read-only")
                )
            ),
        )

        async def _resolve(root_path: str) -> tuple[Path, SimpleNamespace]:
            return REPO_ROOT, _git_info()

        monkeypatch.setattr(controller, "_resolve_root", _resolve)

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
        stored = VibeCodeProjectLink(
            repo_root=root, repo_url=REPO_URL, project_id="p1", project_name="P1"
        )
        monkeypatch.setattr(
            project_links,
            "VibeProjectsStore",
            lambda: SimpleNamespace(
                list_remote_projects=lambda: [stored],
                delete_remote_project=lambda **kw: deleted.append(kw["repo_root"]),
            ),
        )

        async def _raise(root_path: str) -> tuple[Path, SimpleNamespace]:
            raise ProjectLinksInvalidRequest("Not an eligible project root")

        monkeypatch.setattr(controller, "_resolve_root", _raise)
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
                list_remote_projects=lambda: [],
                delete_remote_project=lambda **kw: deleted.append(kw["repo_root"]),
            ),
        )

        async def _raise(root_path: str) -> tuple[Path, SimpleNamespace]:
            raise ProjectLinksInvalidRequest("Not an eligible project root")

        monkeypatch.setattr(controller, "_resolve_root", _raise)
        result = await controller.unlink(str((tmp_path / "gone").resolve()))
        assert result == {"unlinked": True}
        assert deleted == []


class _FakeProjectLinksController:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def list_links(self) -> dict[str, Any]:
        self.calls.append(("list_links", ()))
        return {"projects": [{"projectId": "p1", "repoLocalPaths": ["/repo"]}]}

    async def resolve_root(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("resolve_root", (root_path,)))
        if self.error is not None:
            raise self.error
        return {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "repoLocalPath": "/repo",
                "repoName": "widgets",
                "currentBranch": "main",
                "defaultBranch": "main",
            },
        }

    async def inspect_root(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("inspect_root", (root_path,)))
        return {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "repoLocalPath": "/repo",
                "repoName": "widgets",
                "currentBranch": "main",
                "defaultBranch": "main",
                "repoUrl": "https://github.com/acme/widgets.git",
            },
            "savedLink": {"projectId": "p1", "projectName": "Widgets"},
            "staleLinkCleared": False,
        }

    async def picker_load(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("picker_load", (root_path,)))
        return {
            "root": {
                "repoLocalPath": "/repo",
                "repoName": "widgets",
                "currentBranch": "main",
                "defaultBranch": "main",
            },
            "savedLink": None,
            "staleLinkCleared": False,
            "candidates": {"items": [], "nextCursor": None},
        }

    async def picker_load_more(self, root_path: str, cursor: str) -> dict[str, Any]:
        self.calls.append(("picker_load_more", (root_path, cursor)))
        return {"candidates": {"items": [], "nextCursor": None}, "focusProjectId": None}

    async def create(
        self, root_path: str, name: str, default_branch: str
    ) -> dict[str, Any]:
        self.calls.append(("create", (root_path, name, default_branch)))
        return {
            "link": {"projectId": "p1", "projectName": name, "repoLocalPath": root_path}
        }

    async def link(
        self, root_path: str, project_id: str, project_name: str
    ) -> dict[str, Any]:
        self.calls.append(("link", (root_path, project_id, project_name)))
        return {
            "link": {
                "projectId": project_id,
                "projectName": project_name,
                "repoLocalPath": root_path,
            }
        }

    async def save(
        self, root_path: str, project_id: str, project_name: str, expected_repo_url: str
    ) -> dict[str, Any]:
        self.calls.append((
            "save",
            (root_path, project_id, project_name, expected_repo_url),
        ))
        return {
            "link": {
                "projectId": project_id,
                "projectName": project_name,
                "repoLocalPath": root_path,
            }
        }

    async def unlink(self, root_path: str) -> dict[str, Any]:
        self.calls.append(("unlink", (root_path,)))
        return {"unlinked": True}


class TestProjectLinksJsonRpc:
    def test_methods_are_advertised(self) -> None:
        assert {
            "projectLinks/create",
            "projectLinks/inspectRoot",
            "projectLinks/link",
            "projectLinks/list",
            "projectLinks/picker/load",
            "projectLinks/picker/loadMore",
            "projectLinks/resolveRoot",
            "projectLinks/save",
            "projectLinks/unlink",
        }.issubset(SERVER_METHODS)

    @pytest.mark.asyncio
    async def test_dispatches_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/resolveRoot", {"rootPath": "/repo"}
        )

        assert controller.calls == [("resolve_root", ("/repo",))]
        assert result.response.model_dump(mode="json", by_alias=True) == {
            "eligible": True,
            "rejectReason": None,
            "root": {
                "repoLocalPath": "/repo",
                "repoName": "widgets",
                "currentBranch": "main",
                "defaultBranch": "main",
            },
        }

    @pytest.mark.asyncio
    async def test_dispatches_inspect_root_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/inspectRoot", {"rootPath": "/repo"}
        )

        assert controller.calls == [("inspect_root", ("/repo",))]
        assert (
            result.response.model_dump(mode="json", by_alias=True)["root"]["repoUrl"]
            == "https://github.com/acme/widgets.git"
        )

    @pytest.mark.asyncio
    async def test_dispatches_save_without_attached_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        controller = _FakeProjectLinksController()
        handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))
        monkeypatch.setattr(handler, "_project_links", controller)

        result = await handler.dispatch(
            "projectLinks/save",
            {
                "rootPath": "/repo",
                "projectId": "p1",
                "projectName": "Widgets",
                "expectedRepoUrl": "https://github.com/acme/widgets.git",
            },
        )

        assert controller.calls == [
            ("save", ("/repo", "p1", "Widgets", "https://github.com/acme/widgets.git"))
        ]
        assert result.response.model_dump(mode="json", by_alias=True) == {
            "link": {
                "projectId": "p1",
                "projectName": "Widgets",
                "repoLocalPath": "/repo",
            }
        }

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (ProjectLinksAuthError("no key"), ProtocolErrorCode.UNAUTHORIZED),
            (ProjectLinksInvalidRequest("bad root"), ProtocolErrorCode.INVALID_PARAMS),
            (ProjectLinksInternalError("boom"), ProtocolErrorCode.INTERNAL_ERROR),
        ],
    )
    @pytest.mark.asyncio
    async def test_maps_controller_errors(
        self, monkeypatch: pytest.MonkeyPatch, error: Exception, code: ProtocolErrorCode
    ) -> None:
        handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))
        monkeypatch.setattr(
            handler, "_project_links", _FakeProjectLinksController(error)
        )

        with pytest.raises(RequestFailure) as excinfo:
            await handler.dispatch("projectLinks/resolveRoot", {"rootPath": "/repo"})

        assert excinfo.value.code is code
