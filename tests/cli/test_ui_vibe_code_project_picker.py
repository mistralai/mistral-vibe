from __future__ import annotations

import pytest
from textual.widgets import OptionList

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.app_server.models import (
    VibeCodeGitInfo,
    VibeCodePickerContext,
    VibeCodePickerState,
    VibeCodePickerView,
    VibeCodeProject,
    VibeCodeProjectLink,
    VibeCodeRepository,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.cli.textual_ui.widgets.vibe_code_project import (
    VibeCodeProjectCreateApp,
    VibeCodeProjectPickerApp,
)
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput

REPO_ROOT = "/repo/mistral-vibe"
REPO_URL = "https://github.com/mistralai/mistral-vibe.git"


def _project(project_id: str, name: str, repo_url: str = REPO_URL) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=[VibeCodeRepository(repo_url=repo_url)],
    )


def _link(project: VibeCodeProject, repo_url: str = REPO_URL) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=REPO_ROOT,
        repo_url=repo_url,
        project_id=project.project_id,
        project_name=project.name,
    )


def _view(
    projects: list[VibeCodeProject],
    *,
    next_cursor: str | None = None,
    saved_link: VibeCodeProjectLink | None = None,
    saved_project_link_cleared: bool = False,
    project_repo_remote_changed: bool = False,
) -> VibeCodePickerView:
    return VibeCodePickerView(
        context=VibeCodePickerContext(
            repo_root=REPO_ROOT,
            repo_url=REPO_URL,
            repo_name="mistral-vibe",
            saved_link=saved_link,
        ),
        state=VibeCodePickerState(
            projects=projects, next_cursor=next_cursor, repo_url=REPO_URL
        ),
        git=VibeCodeGitInfo(
            remote_name="origin",
            remote_url=REPO_URL,
            repo="mistral-vibe",
            branch="main",
            default_branch="develop",
        ),
        saved_project_link_cleared=saved_project_link_cleared,
        project_repo_remote_changed=project_repo_remote_changed,
    )


def _linked_view(
    view: VibeCodePickerView, project: VibeCodeProject
) -> VibeCodePickerView:
    return view.model_copy(
        update={
            "context": view.context.model_copy(update={"saved_link": _link(project)})
        }
    )


def _response_error(message: str) -> AppServerResponseError:
    return AppServerResponseError(
        ProtocolError(code=ProtocolErrorCode.INVALID_PARAMS, message=message)
    )


class FakeVibeCodeResource:
    def __init__(
        self,
        view: VibeCodePickerView,
        *,
        resolved_project_id: str | None = None,
        open_error: AppServerResponseError | None = None,
    ) -> None:
        self.view = view
        self.resolved_project_id = resolved_project_id
        self.open_error = open_error
        self.load_more_result: tuple[VibeCodePickerView, str | None] | None = None
        self.create_result: tuple[VibeCodePickerView, VibeCodeProject] | None = None
        self.select_result: tuple[VibeCodePickerView, VibeCodeProject] | None = None
        self.unlink_result: VibeCodePickerView | None = None
        self.recovery_result: tuple[VibeCodePickerView, bool] | None = None
        self.open_calls: list[bool] = []
        self.load_more_calls = 0
        self.create_calls: list[tuple[str, str]] = []
        self.select_calls: list[str] = []
        self.unlink_calls = 0
        self.cancel_calls = 0
        self.recovery_calls = 0

    async def open_projects(
        self, *, for_teleport: bool = False, prompt: str | None = None
    ) -> tuple[VibeCodePickerView, str | None]:
        del prompt
        self.open_calls.append(for_teleport)
        if self.open_error is not None:
            raise self.open_error
        return self.view, self.resolved_project_id

    async def load_more(self) -> tuple[VibeCodePickerView, str | None]:
        self.load_more_calls += 1
        if self.load_more_result is None:
            raise AssertionError("load_more result was not configured")
        return self.load_more_result

    async def create(
        self, *, name: str, default_branch: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        self.create_calls.append((name, default_branch))
        if self.create_result is None:
            raise AssertionError("create result was not configured")
        return self.create_result

    async def select_project(
        self, project_id: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        self.select_calls.append(project_id)
        if self.select_result is None:
            raise AssertionError("select result was not configured")
        return self.select_result

    async def unlink(self) -> VibeCodePickerView:
        self.unlink_calls += 1
        if self.unlink_result is None:
            raise AssertionError("unlink result was not configured")
        return self.unlink_result

    async def cancel_picker(self) -> None:
        self.cancel_calls += 1

    async def recover_stale_link(self) -> tuple[VibeCodePickerView, bool]:
        self.recovery_calls += 1
        if self.recovery_result is None:
            raise AssertionError("recovery result was not configured")
        return self.recovery_result


def _install_resource(
    app: VibeApp, monkeypatch: pytest.MonkeyPatch, resource: object
) -> None:
    monkeypatch.setattr(app.app_server.resources, "vibe_code", resource)


async def _wait_for_command_availability(app: VibeApp) -> None:
    await app._startup_command_availability_ready.wait()


@pytest.mark.asyncio
async def test_vibe_code_project_command_opens_public_picker_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view(
        [_project("mistral-vibe", "Mistral Vibe"), _project("docs", "Docs")],
        next_cursor="next-page",
    )
    resource = FakeVibeCodeResource(view)
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)

        await app._vibe_code_project_command()
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        assert resource.open_calls == [False]
        assert app._vibe_code_project_picker.view == view
        assert [item.option_id for item in picker.items] == [
            "project:docs",
            "project:mistral-vibe",
            "action:load_more",
            "action:create",
        ]
        assert picker.items[0].recommended is True


@pytest.mark.asyncio
async def test_vibe_code_project_command_uses_server_access_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = FakeVibeCodeResource(
        _view([]),
        open_error=_response_error("Vibe Code project access is unavailable."),
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)

        await app._vibe_code_project_command()
        await pilot.pause()

        errors = [str(message._error) for message in app.query(ErrorMessage)]
        assert resource.open_calls == [False]
        assert any("project access is unavailable" in error for error in errors)


@pytest.mark.asyncio
async def test_vibe_code_project_load_more_uses_canonical_public_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_view = _view(
        [_project("mistral-vibe", "Mistral Vibe")], next_cursor="next-page"
    )
    next_view = _view([
        _project("mistral-vibe", "Mistral Vibe"),
        _project("docs", "Docs"),
    ])
    resource = FakeVibeCodeResource(initial_view)
    resource.load_more_result = next_view, "project:docs"
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        await app._vibe_code_project_command()
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_load_more_requested(
            VibeCodeProjectPickerApp.LoadMoreRequested()
        )
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        option_list = picker.query_one(OptionList)
        assert resource.load_more_calls == 1
        assert app._vibe_code_project_picker.view == next_view
        assert [item.option_id for item in picker.items] == [
            "project:docs",
            "project:mistral-vibe",
            "action:create",
        ]
        assert option_list.highlighted_option is not None
        assert option_list.highlighted_option.id == "project:docs"


@pytest.mark.asyncio
async def test_vibe_code_project_create_uses_public_git_and_resource_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_view = _view([_project("mistral-vibe", "Mistral Vibe")])
    created = _project("created", "Renamed Mistral Vibe")
    created_view = _view([created, *initial_view.state.projects])
    selected_view = _linked_view(created_view, created)
    resource = FakeVibeCodeResource(initial_view)
    resource.create_result = created_view, created
    resource.select_result = selected_view, created
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        await app._vibe_code_project_command()
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_create_requested(
            VibeCodeProjectPickerApp.CreateRequested("Custom Mistral Vibe")
        )
        await pilot.pause()

        create_app = app.query_one(VibeCodeProjectCreateApp)
        default_branch_input = create_app.query_one(
            "#vibecodeprojectcreate-default-branch", VscodeCompatInput
        )
        assert default_branch_input.value == "develop"

        await app.on_vibe_code_project_create_app_submitted(
            VibeCodeProjectCreateApp.Submitted("Renamed Mistral Vibe", "release")
        )
        await pilot.pause()

        assert resource.create_calls == [("Renamed Mistral Vibe", "release")]
        assert resource.select_calls == ["created"]
        assert app._vibe_code_project_picker.view == selected_view


@pytest.mark.asyncio
async def test_vibe_code_project_selection_applies_server_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project("mistral-vibe", "Mistral Vibe")
    initial_view = _view([project])
    selected_view = _linked_view(initial_view, project)
    resource = FakeVibeCodeResource(initial_view)
    resource.select_result = selected_view, project
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        await app._vibe_code_project_command()
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_project_selected(
            VibeCodeProjectPickerApp.ProjectSelected(project.project_id, project.name)
        )
        await pilot.pause()

        assert resource.select_calls == [project.project_id]
        assert app._vibe_code_project_picker.view == selected_view


@pytest.mark.asyncio
async def test_vibe_code_project_unlink_applies_server_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project("mistral-vibe", "Mistral Vibe")
    linked_view = _view([project], saved_link=_link(project))
    unlinked_view = _view([project], saved_project_link_cleared=True)
    resource = FakeVibeCodeResource(linked_view)
    resource.unlink_result = unlinked_view
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        await app._vibe_code_project_command()
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_unlink_requested(
            VibeCodeProjectPickerApp.UnlinkRequested()
        )
        await pilot.pause()

        assert resource.unlink_calls == 1
        assert app._vibe_code_project_picker.view == unlinked_view
        assert app._vibe_code_project_picker.context is not None
        assert app._vibe_code_project_picker.context.saved_link is None


@pytest.mark.asyncio
async def test_vibe_code_project_cancel_uses_resource_and_clears_ui_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = FakeVibeCodeResource(_view([]))
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        await app._vibe_code_project_command()
        await pilot.pause()
        app._vibe_code_project_picker.teleport_pending = True
        app._vibe_code_project_picker.teleport_prompt = "continue remotely"

        await app.on_vibe_code_project_picker_app_cancelled(
            VibeCodeProjectPickerApp.Cancelled()
        )
        await pilot.pause()

        assert resource.cancel_calls == 1
        assert app._vibe_code_project_picker.teleport_pending is False
        assert app._vibe_code_project_picker.teleport_prompt is None


@pytest.mark.asyncio
async def test_teleport_resolution_uses_server_resolved_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project("mistral-vibe", "Mistral Vibe")
    view = _view([project], saved_link=_link(project))
    resource = FakeVibeCodeResource(view, resolved_project_id=project.project_id)
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)

        project_id = await app._resolve_vibe_code_project_for_teleport(
            "continue remotely"
        )
        await pilot.pause()

        assert project_id == project.project_id
        assert resource.open_calls == [True]
        assert app._vibe_code_project_picker.view == view
        assert list(app.query(VibeCodeProjectPickerApp)) == []


@pytest.mark.asyncio
async def test_changed_remote_public_view_opens_picker_for_teleport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = _view([], saved_project_link_cleared=True, project_repo_remote_changed=True)
    resource = FakeVibeCodeResource(view)
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)

        project_id = await app._resolve_vibe_code_project_for_teleport(
            "continue remotely"
        )
        await pilot.pause()

        assert project_id is None
        assert app._vibe_code_project_picker.view == view
        assert app._vibe_code_project_picker.teleport_pending is True
        assert app._vibe_code_project_picker.teleport_prompt == "continue remotely"
        app.query_one(VibeCodeProjectPickerApp)


@pytest.mark.asyncio
async def test_stale_link_recovery_replaces_ui_with_server_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale = _project("stale", "Stale project")
    initial_view = _view([], saved_link=_link(stale))
    recovered_view = _view([_project("replacement", "Replacement")])
    resource = FakeVibeCodeResource(initial_view)
    resource.recovery_result = recovered_view, True
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)
        app._vibe_code_project_picker.view = initial_view

        recovered = await app._show_vibe_code_project_picker_after_saved_link_failure(
            "continue remotely"
        )
        await pilot.pause()

        assert recovered is True
        assert resource.recovery_calls == 1
        assert app._vibe_code_project_picker.view == recovered_view
        assert app._vibe_code_project_picker.teleport_pending is True
        assert app._vibe_code_project_picker.teleport_prompt == "continue remotely"
        app.query_one(VibeCodeProjectPickerApp)


@pytest.mark.parametrize(
    "message", ["Teleport requires a git repository.", "Projects unavailable."]
)
@pytest.mark.asyncio
async def test_vibe_code_project_command_displays_app_server_errors(
    monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    resource = FakeVibeCodeResource(_view([]), open_error=_response_error(message))
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    async with app.run_test() as pilot:
        _install_resource(app, monkeypatch, resource)
        await _wait_for_command_availability(app)

        await app._vibe_code_project_command()
        await pilot.pause()

        errors = [str(error._error) for error in app.query(ErrorMessage)]
        assert any(message in error for error in errors)
