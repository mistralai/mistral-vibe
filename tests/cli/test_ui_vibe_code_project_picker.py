from __future__ import annotations

from collections.abc import Awaitable
from pathlib import Path
from typing import cast

import pytest
from textual.widgets import OptionList

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.plan_offer.decide_plan_offer import PlanInfo
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.cli.textual_ui.widgets.vibe_code_project import (
    VibeCodeProjectCreateApp,
    VibeCodeProjectPickerApp,
)
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput
from vibe.core.agent_loop import TeleportError
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    ProjectRepository,
    TeleportProjectResolution,
    VibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectCreateResult,
    VibeCodeProjectLink,
    VibeCodeProjectLoadMoreResult,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
    normalize_repo_url,
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
        self.saved_links: list[VibeCodeProjectLink] = []
        self.cleared_contexts: list[ProjectPickerContext] = []

    async def load_initial(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        self.initial_calls.append(git_info)
        if isinstance(self.initial, VibeCodeProjectApiError):
            raise self.initial
        return self.initial

    async def load_initial_for_teleport(
        self, git_info: GitRepoInfo
    ) -> VibeCodeProjectPickerInitialData:
        return await self.load_initial(git_info)

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

    def save_project_link(
        self, *, context: ProjectPickerContext, project_id: str, project_name: str
    ) -> VibeCodeProjectLink:
        link = VibeCodeProjectLink(
            repo_root=context.repo_root,
            repo_url=context.repo_url,
            project_id=project_id,
            project_name=project_name,
        )
        self.saved_links.append(link)
        return link

    def clear_project_link(self, context: ProjectPickerContext) -> None:
        self.cleared_contexts.append(context)

    def resolve_project_for_teleport(
        self, initial_data: VibeCodeProjectPickerInitialData
    ) -> TeleportProjectResolution:
        saved_link = initial_data.context.saved_link
        if saved_link is not None and normalize_repo_url(
            saved_link.repo_url
        ) == normalize_repo_url(initial_data.context.repo_url):
            return TeleportProjectResolution(
                project_id=saved_link.project_id,
                initial_data=initial_data,
                stale_link_cleared=False,
            )

        if saved_link is None:
            return TeleportProjectResolution(
                project_id=None, initial_data=initial_data, stale_link_cleared=False
            )

        self.clear_project_link(initial_data.context)
        return TeleportProjectResolution(
            project_id=None,
            initial_data=VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=initial_data.context.repo_root,
                    repo_url=initial_data.context.repo_url,
                    repo_name=initial_data.context.repo_name,
                    saved_link=None,
                ),
                state=initial_data.state,
            ),
            stale_link_cleared=True,
        )


def _context(saved_link: VibeCodeProjectLink | None = None) -> ProjectPickerContext:
    return ProjectPickerContext(
        repo_root=Path("/repo/mistral-vibe"),
        repo_url="https://github.com/mistralai/mistral-vibe.git",
        repo_name="mistral-vibe",
        saved_link=saved_link,
    )


def _link(
    project_id: str = "mistral-vibe",
    repo_url: str = "https://github.com/mistralai/mistral-vibe.git",
) -> VibeCodeProjectLink:
    return VibeCodeProjectLink(
        repo_root=Path("/repo/mistral-vibe"),
        repo_url=repo_url,
        project_id=project_id,
        project_name="Mistral Vibe",
    )


def _remote_project_events(telemetry_events: list[dict]) -> list[dict]:
    return [
        event
        for event in telemetry_events
        if event["event_name"] == "vibe.remote_project_configured"
    ]


def _assert_private_project_values_not_in_payload(payload: dict) -> None:
    serialized = str(payload)
    assert "Mistral Vibe" not in serialized
    assert "mistral-vibe.git" not in serialized
    assert "/repo/mistral-vibe" not in serialized


@pytest.mark.asyncio
async def test_vibe_code_project_command_fetches_projects_and_opens_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
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
async def test_vibe_code_project_command_reuses_teleport_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

    def build_service() -> FakePickerService:
        raise AssertionError("remote-project should stop before loading projects")

    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", build_service)

    async with app.run_test() as pilot:
        app._plan_info = PlanInfo(WhoAmIPlanType.API, "FREE")
        await app._vibe_code_project_command()
        await pilot.pause()

        errors = [str(message._error) for message in app.query(ErrorMessage)]
        assert any("Vibe Pro subscription" in error for error in errors)


@pytest.mark.asyncio
async def test_vibe_code_project_load_more_fetches_next_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
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
    monkeypatch: pytest.MonkeyPatch, telemetry_events: list[dict]
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
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
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

        assert len(service.create_calls) == 1
        name, default_branch, git_info, state = service.create_calls[0]
        assert name == "Renamed Mistral Vibe"
        assert default_branch == "release"
        assert git_info.repo == "mistral-vibe"
        assert state == initial_state
        assert service.saved_links == [
            VibeCodeProjectLink(
                repo_root=Path("/repo/mistral-vibe"),
                repo_url="https://github.com/mistralai/mistral-vibe.git",
                project_id="created",
                project_name="Renamed Mistral Vibe",
            )
        ]
        event = _remote_project_events(telemetry_events)[-1]
        assert {
            "outcome": "created",
            "project_picker_shown": True,
            "project_selection_source": "created_project",
            "project_candidate_count_loaded": 2,
            "project_multi_repo_match_count": 0,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }.items() <= event["properties"].items()
        _assert_private_project_values_not_in_payload(event["properties"])


@pytest.mark.asyncio
async def test_vibe_code_project_selection_saves_link(
    monkeypatch: pytest.MonkeyPatch, telemetry_events: list[dict]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
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
                    )
                ],
                next_cursor=None,
            ),
        )
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_project_selected(
            VibeCodeProjectPickerApp.ProjectSelected("mistral-vibe", "Mistral Vibe")
        )
        await pilot.pause()

    assert service.saved_links == [_link("mistral-vibe")]
    event = _remote_project_events(telemetry_events)[-1]
    assert {
        "outcome": "configured",
        "project_picker_shown": True,
        "project_selection_source": "selected_existing",
        "project_candidate_count_loaded": 1,
        "project_multi_repo_match_count": 0,
        "saved_project_link_cleared": False,
        "project_repo_remote_changed": False,
    }.items() <= event["properties"].items()
    _assert_private_project_values_not_in_payload(event["properties"])


@pytest.mark.asyncio
async def test_vibe_code_project_unlink_clears_saved_link(
    monkeypatch: pytest.MonkeyPatch, telemetry_events: list[dict]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(saved_link=_link()),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_unlink_requested(
            VibeCodeProjectPickerApp.UnlinkRequested()
        )
        await pilot.pause()

    assert service.cleared_contexts == [_context(saved_link=_link())]
    event = _remote_project_events(telemetry_events)[-1]
    assert {
        "outcome": "unlinked",
        "project_picker_shown": True,
        "project_selection_source": "saved_link",
        "project_candidate_count_loaded": 0,
        "project_multi_repo_match_count": 0,
        "saved_project_link_cleared": True,
        "project_repo_remote_changed": False,
    }.items() <= event["properties"].items()
    _assert_private_project_values_not_in_payload(event["properties"])


@pytest.mark.asyncio
async def test_vibe_code_project_cancel_emits_remote_project_telemetry(
    monkeypatch: pytest.MonkeyPatch, telemetry_events: list[dict]
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    async with app.run_test() as pilot:
        await app.on_chat_input_container_submitted(
            ChatInputContainer.Submitted("/remote-project")
        )
        await pilot.pause()

        await app.on_vibe_code_project_picker_app_cancelled(
            VibeCodeProjectPickerApp.Cancelled()
        )
        await pilot.pause()

    event = _remote_project_events(telemetry_events)[-1]
    assert {
        "outcome": "cancelled",
        "project_picker_shown": True,
        "project_selection_source": "cancelled",
        "project_candidate_count_loaded": 0,
        "project_multi_repo_match_count": 0,
        "saved_project_link_cleared": False,
        "project_repo_remote_changed": False,
    }.items() <= event["properties"].items()
    _assert_private_project_values_not_in_payload(event["properties"])


@pytest.mark.asyncio
async def test_teleport_with_saved_project_link_uses_project_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(saved_link=_link("mistral-vibe")),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    app._plan_info = PlanInfo(WhoAmIPlanType.CHAT, "INDIVIDUAL")
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    teleport_calls: list[tuple[str | None, str | None]] = []
    project_picker_payloads: list[dict | None] = []
    worker_coroutines: list[Awaitable[None]] = []

    def fake_teleport(
        prompt: str | None = None,
        *,
        project_id: str | None = None,
        project_picker: dict | None = None,
    ):
        teleport_calls.append((prompt, project_id))
        project_picker_payloads.append(project_picker)

        async def noop() -> None:
            return None

        return noop()

    def fake_run_worker(coro: Awaitable[None], *, exclusive: bool = True) -> None:
        worker_coroutines.append(coro)

    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "_teleport", fake_teleport)
        monkeypatch.setattr(app, "run_worker", fake_run_worker)
        await app._handle_teleport_command("continue remotely", show_message=False)
        await pilot.pause()

    assert teleport_calls == [("continue remotely", "mistral-vibe")]
    assert project_picker_payloads == [
        {
            "project_picker_shown": False,
            "project_selection_source": "saved_link",
            "project_candidate_count_loaded": 0,
            "project_multi_repo_match_count": 0,
            "saved_project_link_cleared": False,
            "project_repo_remote_changed": False,
        }
    ]
    assert len(worker_coroutines) == 1
    await worker_coroutines[0]


@pytest.mark.asyncio
async def test_teleport_with_changed_remote_opens_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository", lambda: FakeGitRepository()
    )
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(
                saved_link=_link(
                    "mistral-vibe", repo_url="https://github.com/mistralai/old.git"
                )
            ),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    app._plan_info = PlanInfo(WhoAmIPlanType.CHAT, "INDIVIDUAL")
    monkeypatch.setattr(app, "_build_vibe_code_project_picker_service", lambda: service)

    teleport_calls: list[tuple[str | None, str | None]] = []

    def fake_teleport(
        prompt: str | None = None,
        *,
        project_id: str | None = None,
        project_picker: dict | None = None,
    ):
        teleport_calls.append((prompt, project_id))

        async def noop() -> None:
            return None

        return noop()

    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "_teleport", fake_teleport)
        await app._handle_teleport_command("continue remotely", show_message=False)
        await pilot.pause()

        app.query_one(VibeCodeProjectPickerApp)

    assert teleport_calls == []
    assert app._vibe_code_project_picker.context == _context(saved_link=None)


@pytest.mark.parametrize(
    "error_message",
    [
        'Vibe Code Web start failed (status 404): {"error":"Project not found"}',
        'Vibe Code Web start failed (status 403): {"error":"Project forbidden"}',
    ],
)
@pytest.mark.asyncio
async def test_teleport_stale_saved_project_clears_link_and_reopens_picker(
    monkeypatch: pytest.MonkeyPatch, error_message: str
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))
    service = FakePickerService(
        initial=VibeCodeProjectPickerInitialData(
            context=_context(saved_link=_link()),
            state=VibeCodeProjectPickerState(projects=[], next_cursor=None),
        )
    )
    app._vibe_code_project_picker.service = cast(VibeCodeProjectPickerService, service)
    app._vibe_code_project_picker.context = _context(saved_link=_link())
    app._vibe_code_project_picker.picker_state = VibeCodeProjectPickerState(
        projects=[], next_cursor=None
    )
    app._vibe_code_project_picker.git_info = GitRepoInfo(
        remote_name="origin",
        remote_url="https://github.com/mistralai/mistral-vibe.git",
        owner="mistralai",
        repo="mistral-vibe",
        branch="main",
        commit="abc123",
        diff="",
        default_branch="develop",
    )

    async def failing_teleport(
        _prompt: str | None,
        *,
        project_id: str | None = None,
        project_picker: dict | None = None,
    ):
        if False:
            yield None
        raise TeleportError(error_message)

    monkeypatch.setattr(app.agent_loop, "teleport_to_vibe_code", failing_teleport)

    async with app.run_test() as pilot:
        await app._teleport("continue remotely", project_id="mistral-vibe")
        await pilot.pause()
        app.query_one(VibeCodeProjectPickerApp)

    assert service.cleared_contexts == [_context(saved_link=_link())]
    assert app._vibe_code_project_picker.context == _context(saved_link=None)
    assert app._vibe_code_project_picker.teleport_pending is True
    assert app._vibe_code_project_picker.teleport_prompt == "continue remotely"


@pytest.mark.asyncio
async def test_vibe_code_project_command_reports_git_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.make_git_repository",
        lambda: FakeFailingGitRepository(),
    )
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

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
    app = build_test_vibe_app(config=build_test_vibe_config(vibe_code_enabled=True))

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
