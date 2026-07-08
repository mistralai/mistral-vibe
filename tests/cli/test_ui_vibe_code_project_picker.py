from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import OptionList

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.cli.textual_ui.widgets.vibe_code_project import (
    VibeCodeProjectCreateApp,
    VibeCodeProjectPickerApp,
)
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    VibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectCreateResult,
    VibeCodeProjectLoadMoreResult,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerState,
)


class FakeGitRepository:
    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> FakeGitRepository:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_info(self) -> GitRepoInfo:
        return GitRepoInfo(
            remote_name="origin",
            remote_url="https://github.com/mistralai/mistral-vibe.git",
            owner="mistralai",
            repo="mistral-vibe",
            branch="main",
            commit="abc123",
            diff="",
            default_branch="develop",
        )


class FakeFailingGitRepository:
    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> FakeFailingGitRepository:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get_info(self) -> GitRepoInfo:
        raise ServiceTeleportError("Teleport requires a git repository.")


def _project(
    project_id: str, name: str, repo_url: str, *, is_read_only: bool = False
) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project_id,
        name=name,
        repositories=(ProjectRepository(repo_url=repo_url),),
        is_read_only=is_read_only,
    )


class FakePickerService:
    def __init__(
        self,
        *,
        initial: VibeCodeProjectPickerInitialData | VibeCodeProjectApiError,
        load_more: VibeCodeProjectLoadMoreResult
        | VibeCodeProjectApiError
        | None = None,
    ) -> None:
        self.initial = initial
        self.load_more_result = load_more
        self.initial_calls: list[GitRepoInfo] = []
        self.load_more_calls: list[VibeCodeProjectPickerState] = []
        self.create_calls: list[
            tuple[str, str, GitRepoInfo, VibeCodeProjectPickerState]
        ] = []

    async def load_initial(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        self.initial_calls.append(git_info)
        if isinstance(self.initial, VibeCodeProjectApiError):
            raise self.initial
        return self.initial

    async def load_more(
        self, state: VibeCodeProjectPickerState
    ) -> VibeCodeProjectLoadMoreResult:
        self.load_more_calls.append(state)
        if isinstance(self.load_more_result, VibeCodeProjectApiError):
            raise self.load_more_result
        assert self.load_more_result is not None
        return self.load_more_result

    async def create_project(
        self,
        *,
        name: str,
        default_branch: str,
        git_info: GitRepoInfo,
        state: VibeCodeProjectPickerState,
    ) -> VibeCodeProjectCreateResult:
        self.create_calls.append((name, default_branch, git_info, state))
        project = _project(
            "created", name, "https://github.com/mistralai/mistral-vibe.git"
        )
        return VibeCodeProjectCreateResult(
            state=VibeCodeProjectPickerState(
                projects=[project, *state.projects], next_cursor=state.next_cursor
            ),
            project=project,
        )


def _context() -> ProjectPickerContext:
    return ProjectPickerContext(
        organization_id="",
        workspace_id="",
        repo_root=Path("/repo/mistral-vibe"),
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        repo_name="mistral-vibe",
    )


@pytest.mark.asyncio
async def test_vibe_code_project_command_fetches_projects_and_opens_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    app = build_test_vibe_app(
        config=build_test_vibe_config(
            vibe_code_enabled=True, experimental_vibe_code_project_picker_enabled=True
        )
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(),
            state=VibeCodeProjectPickerState(
                projects=[
                    _project(
                        "mistral-vibe",
                        "Mistral Vibe",
                        "https://github.com/mistralai/mistral-vibe.git",
                    ),
                    _project(
                        "docs", "Docs", "https://github.com/mistralai/mistral-vibe.git"
                    ),
                ],
                next_cursor="next-page",
            ),
        )
    )

    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)
    loading_statuses: list[str] = []

    async def ensure_loading_widget(
        status: str = "Generating", *, show_hint: bool = True
    ) -> None:
        loading_statuses.append(status)

    monkeypatch.setattr(app, "_ensure_loading_widget", ensure_loading_widget)

    async with app.run_test() as pilot:
        assert app.commands.get_command_name("/remote-project") == "remote-project"

        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        assert len(service.initial_calls) == 1
        assert "Loading Vibe Code projects" in loading_statuses
        assert [item.option_id for item in picker.items] == [
            "project:docs",
            "project:mistral-vibe",
            "action:load_more",
            "action:create",
        ]
        assert picker.items[0].recommended is True


@pytest.mark.asyncio
async def test_vibe_code_project_load_more_fetches_next_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    app = build_test_vibe_app(
        config=build_test_vibe_config(
            vibe_code_enabled=True, experimental_vibe_code_project_picker_enabled=True
        )
    )
    initial_state = VibeCodeProjectPickerState(
        projects=[
            _project(
                "mistral-vibe",
                "Mistral Vibe",
                "https://github.com/mistralai/mistral-vibe.git",
            )
        ],
        next_cursor="next-page",
    )
    next_state = VibeCodeProjectPickerState(
        projects=[
            *initial_state.projects,
            _project("docs", "Docs", "https://github.com/mistralai/mistral-vibe.git"),
        ],
        next_cursor=None,
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(), state=initial_state
        ),
        load_more=VibeCodeProjectLoadMoreResult(
            state=next_state, focus_project_id="docs"
        ),
    )

    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)
    loading_statuses: list[str] = []

    async def ensure_loading_widget(
        status: str = "Generating", *, show_hint: bool = True
    ) -> None:
        loading_statuses.append(status)

    monkeypatch.setattr(app, "_ensure_loading_widget", ensure_loading_widget)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_load_more_requested(
            VibeCodeProjectPickerApp.LoadMoreRequested()
        )
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        option_list = picker.query_one(OptionList)
        assert service.load_more_calls == [initial_state]
        assert "Loading more projects" in loading_statuses
        assert [item.option_id for item in picker.items] == [
            "project:docs",
            "project:mistral-vibe",
            "action:create",
        ]
        assert option_list.highlighted_option is not None
        assert option_list.highlighted_option.id == "project:docs"


@pytest.mark.asyncio
async def test_vibe_code_project_create_opens_name_form_and_creates_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    initial_state = VibeCodeProjectPickerState(
        projects=[
            _project(
                "mistral-vibe",
                "Mistral Vibe",
                "https://github.com/mistralai/mistral-vibe.git",
            )
        ],
        next_cursor=None,
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(), state=initial_state
        )
    )
    app = build_test_vibe_app(
        config=build_test_vibe_config(
            vibe_code_enabled=True, experimental_vibe_code_project_picker_enabled=True
        )
    )
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        removed_after_create_mounted: list[bool] = []
        original_remove = picker.remove

        async def remove_picker() -> object:
            removed_after_create_mounted.append(
                bool(list(app.query(VibeCodeProjectCreateApp)))
            )
            return await original_remove()

        monkeypatch.setattr(picker, "remove", remove_picker)

        await app.on_vibe_code_project_picker_app_create_requested(
            VibeCodeProjectPickerApp.CreateRequested("Custom Mistral Vibe")
        )
        await pilot.pause()

        create_app = app.query_one(VibeCodeProjectCreateApp)
        assert create_app is not None
        default_branch_input = create_app.query_one(
            "#vibecodeprojectcreate-default-branch", VscodeCompatInput
        )
        assert default_branch_input.value == "develop"
        assert removed_after_create_mounted == [True]

        await app.on_vibe_code_project_create_app_submitted(
            VibeCodeProjectCreateApp.Submitted("Renamed Mistral Vibe", "release")
        )
        await pilot.pause()

        picker = app.query_one(VibeCodeProjectPickerApp)
        option_list = picker.query_one(OptionList)
        assert len(service.create_calls) == 1
        name, default_branch, git_info, state = service.create_calls[0]
        assert name == "Renamed Mistral Vibe"
        assert default_branch == "release"
        assert git_info.repo == "mistral-vibe"
        assert state == initial_state
        assert [item.option_id for item in picker.items] == [
            "project:mistral-vibe",
            "project:created",
            "action:create",
        ]
        assert option_list.highlighted_option is not None
        assert option_list.highlighted_option.id == "project:created"


@pytest.mark.asyncio
async def test_vibe_code_project_command_reports_git_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository",
        lambda: FakeFailingGitRepository(),
    )
    app = build_test_vibe_app(
        config=build_test_vibe_config(
            vibe_code_enabled=True, experimental_vibe_code_project_picker_enabled=True
        )
    )

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        errors = [str(message._error) for message in app.query(ErrorMessage)]
        assert any("git repository" in error for error in errors)


@pytest.mark.asyncio
async def test_vibe_code_project_command_reports_project_api_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    app = build_test_vibe_app(
        config=build_test_vibe_config(
            vibe_code_enabled=True, experimental_vibe_code_project_picker_enabled=True
        )
    )

    service = FakePickerService(
        initial=VibeCodeProjectApiError("Projects unavailable.")
    )

    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        errors = [str(message._error) for message in app.query(ErrorMessage)]
        assert any("Projects unavailable." in error for error in errors)
