from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from vibe.app_server._execution import (
    ActiveSessionExecution,
    SessionExecution,
    SessionExecutionKind,
    cancel_tasks,
)
from vibe.app_server._model import ProtocolModel
from vibe.app_server.models import (
    AccountActionKind,
    AccountView,
    PublicError,
    TeleportCheckingGit,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    TeleportPushing,
    TeleportPushRequired,
    TeleportStartingWorkflow,
    TeleportSummarizingContext,
    VibeCodeGitInfo,
    VibeCodePickerContext,
    VibeCodePickerPurpose,
    VibeCodePickerState,
    VibeCodePickerView,
    VibeCodeProject,
    VibeCodeProjectLink,
    VibeCodeRepository,
)
from vibe.app_server.protocol import TeleportEventParams, TeleportStartParams
from vibe.core.agent_loop import AgentLoop, TeleportError
from vibe.core.telemetry.types import (
    ProjectPickerTelemetryPayload,
    ProjectSelectionSource,
    TeleportFailureStage,
)
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.telemetry import send_teleport_early_failure_telemetry
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
    TeleportSummarizingContextEvent,
)
from vibe.core.types import Role
from vibe.core.vibe_code_project import (
    ProjectPickerContext,
    VibeCodeProject as CoreVibeCodeProject,
    VibeCodeProjectApiError,
    VibeCodeProjectPickerInitialData,
    VibeCodeProjectPickerService,
    VibeCodeProjectPickerState,
    VibeProjectsStore,
    build_project_picker_telemetry,
    is_project_linked_to_repo,
    is_saved_project_stale_error,
)
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.teleport.git import GitRepoInfo

type Notify = Callable[[str, ProtocolModel], Awaitable[None]]
type ReadAccount = Callable[[], Awaitable[AccountView]]


class VibeCodeError(RuntimeError):
    pass


class VibeCodeConflictError(VibeCodeError):
    pass


class VibeCodeAccessError(VibeCodeError):
    pass


@dataclass(frozen=True, slots=True)
class ReservedTeleport:
    project_picker: ProjectPickerTelemetryPayload
    execution: ActiveSessionExecution


class VibeCodeController:
    def __init__(
        self,
        agent_loop: AgentLoop,
        notify: Notify,
        execution: SessionExecution,
        read_account: ReadAccount,
    ) -> None:
        self._agent_loop = agent_loop
        self._notify = notify
        self._execution = execution
        self._read_account = read_account
        self._store = VibeProjectsStore()
        self._service: VibeCodeProjectPickerService | None = None
        self._data: VibeCodeProjectPickerInitialData | None = None
        self._git: GitRepoInfo | None = None
        self._picker_id: str | None = None
        self._picker_purpose: VibeCodePickerPurpose | None = None
        self._selected_project_id: str | None = None
        self._created_project_ids: set[str] = set()
        self._picker_lock = asyncio.Lock()
        self._saved_link_cleared = False
        self._remote_changed = False
        self._project_picker: ProjectPickerTelemetryPayload | None = None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._reserved_operations: dict[str, ReservedTeleport] = {}
        self._push_responses: dict[str, asyncio.Future[bool]] = {}

    async def open(
        self, *, purpose: VibeCodePickerPurpose, prompt: str | None = None
    ) -> tuple[str, VibeCodePickerView, str | None]:
        self._execution.require_idle()
        if purpose == "teleport":
            await self._require_teleport_available(prompt)
        async with self._picker_lock:
            self._reset_picker_state()
            service = self._make_service()
            git = await self._read_git()
            try:
                data = (
                    await service.load_initial_for_teleport(git)
                    if purpose == "teleport"
                    else await service.load_initial(git)
                )
            except VibeCodeProjectApiError as exc:
                raise VibeCodeError(str(exc)) from exc

            resolved_project_id: str | None = None
            if purpose == "teleport":
                resolution = await asyncio.to_thread(
                    service.resolve_project_for_teleport, data
                )
                data = resolution.initial_data
                resolved_project_id = resolution.project_id
                self._saved_link_cleared = resolution.stale_link_cleared
                self._remote_changed = resolution.stale_link_cleared

            picker_id = str(uuid4())
            self._picker_id = picker_id
            self._picker_purpose = purpose
            self._service = service
            self._git = git
            self._data = data
            self._selected_project_id = resolved_project_id
            self._project_picker = (
                self._picker_telemetry(source="saved_link", shown=False)
                if resolved_project_id is not None
                else None
            )
            return picker_id, self.view(), resolved_project_id

    async def load_more(self, picker_id: str) -> tuple[VibeCodePickerView, str | None]:
        self._execution.require_idle()
        async with self._picker_lock:
            service, data, _ = self._require_picker(picker_id)
            if not data.state.has_more:
                return self.view(), None
            try:
                result = await service.load_more(data.state)
            except VibeCodeProjectApiError as exc:
                raise VibeCodeError(str(exc)) from exc
            self._data = VibeCodeProjectPickerInitialData(
                context=data.context, state=result.state
            )
            return self.view(), result.focus_option_id

    async def create(
        self, *, picker_id: str, name: str, default_branch: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        self._execution.require_idle()
        async with self._picker_lock:
            service, data, git = self._require_picker(picker_id)
            try:
                result = await service.create_project(
                    name=name,
                    default_branch=default_branch,
                    git_info=git,
                    state=data.state,
                )
            except VibeCodeProjectApiError as exc:
                raise VibeCodeError(str(exc)) from exc
            self._data = VibeCodeProjectPickerInitialData(
                context=data.context, state=result.state
            )
            self._created_project_ids.add(result.project.project_id)
            return self.view(), _project(result.project)

    async def select(
        self, *, picker_id: str, project_id: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        self._execution.require_idle()
        async with self._picker_lock:
            service, data, _ = self._require_picker(picker_id)
            project = next(
                (
                    candidate
                    for candidate in data.state.projects
                    if candidate.project_id == project_id
                ),
                None,
            )
            if project is None:
                raise VibeCodeError(f"Unknown Vibe Code project: {project_id}")
            if project.is_read_only or not is_project_linked_to_repo(
                project, data.context.repo_url
            ):
                raise VibeCodeError(
                    "The selected Vibe Code project is not available for this repository"
                )
            link = await asyncio.to_thread(
                service.save_project_link,
                context=data.context,
                project_id=project.project_id,
                project_name=project.name,
            )
            self._data = VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=data.context.repo_root,
                    repo_url=data.context.repo_url,
                    repo_name=data.context.repo_name,
                    saved_link=link,
                ),
                state=data.state,
            )
            self._selected_project_id = project.project_id
            source: ProjectSelectionSource = (
                "created_project"
                if project.project_id in self._created_project_ids
                else "selected_existing"
            )
            payload = self._picker_telemetry(source=source, shown=True)
            if self._picker_purpose == "teleport":
                self._project_picker = payload
            else:
                self._agent_loop.telemetry_client.send_remote_project_configured(
                    outcome=(
                        "created" if source == "created_project" else "configured"
                    ),
                    project_picker=payload,
                )
            return self.view(), _project(project)

    async def unlink(self, picker_id: str) -> VibeCodePickerView:
        self._execution.require_idle()
        async with self._picker_lock:
            service, data, _ = self._require_picker(picker_id)
            await asyncio.to_thread(service.clear_project_link, data.context)
            self._saved_link_cleared = True
            self._selected_project_id = None
            self._data = VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=data.context.repo_root,
                    repo_url=data.context.repo_url,
                    repo_name=data.context.repo_name,
                    saved_link=None,
                ),
                state=data.state,
            )
            payload = self._picker_telemetry(source="saved_link", shown=True)
            if self._picker_purpose == "teleport":
                self._send_picker_cancelled(payload)
                self._project_picker = None
            else:
                self._agent_loop.telemetry_client.send_remote_project_configured(
                    outcome="unlinked", project_picker=payload
                )
            return self.view()

    async def cancel_picker(self, picker_id: str) -> None:
        self._execution.require_idle()
        async with self._picker_lock:
            self._require_picker(picker_id)
            payload = self._picker_telemetry(source="cancelled", shown=True)
            if self._picker_purpose == "teleport":
                self._send_picker_cancelled(payload)
            else:
                self._agent_loop.telemetry_client.send_remote_project_configured(
                    outcome="cancelled", project_picker=payload
                )
            self._reset_picker_state()

    async def recover_stale_link(
        self, picker_id: str
    ) -> tuple[VibeCodePickerView, bool]:
        self._execution.require_idle()
        async with self._picker_lock:
            service, data, git = self._require_picker(picker_id)
            await asyncio.to_thread(service.clear_project_link, data.context)
            self._saved_link_cleared = True
            self._selected_project_id = None
            self._project_picker = None
            self._data = VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=data.context.repo_root,
                    repo_url=data.context.repo_url,
                    repo_name=data.context.repo_name,
                    saved_link=None,
                ),
                state=data.state,
            )
            try:
                initial = await service.load_initial(git)
            except VibeCodeProjectApiError:
                return self.view(), False
            self._data = VibeCodeProjectPickerInitialData(
                context=ProjectPickerContext(
                    repo_root=initial.context.repo_root,
                    repo_url=initial.context.repo_url,
                    repo_name=initial.context.repo_name,
                    saved_link=None,
                ),
                state=initial.state,
            )
            return self.view(), True

    def start_teleport(self, params: TeleportStartParams) -> None:
        reservation = self._reserved_operations.pop(params.operation_id, None)
        if reservation is None:
            raise VibeCodeConflictError(
                f"Teleport operation was not reserved: {params.operation_id}"
            )
        task = asyncio.create_task(
            self._run_teleport(
                params, reservation.project_picker, reservation.execution
            )
        )
        self._tasks[params.operation_id] = task
        task.add_done_callback(
            lambda completed: self._teleport_done(params.operation_id, completed)
        )

    async def reserve_teleport(self, params: TeleportStartParams) -> None:
        async with self._picker_lock:
            self._require_picker(params.picker_id)
            if self._picker_purpose != "teleport":
                raise VibeCodeConflictError(
                    "The active project picker was not opened for teleport"
                )
            if self._selected_project_id != params.project_id:
                raise VibeCodeConflictError(
                    "The teleport project does not match the selected project"
                )
            if self._project_picker is None:
                raise VibeCodeConflictError(
                    "Teleport project selection is not complete"
                )
            execution = self._execution.begin(
                SessionExecutionKind.TELEPORT, params.operation_id
            )
            self._reserved_operations[params.operation_id] = ReservedTeleport(
                project_picker=self._project_picker, execution=execution
            )

    def _teleport_done(self, operation_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(operation_id, None)
        if task.cancelled():
            return
        if exc := task.exception():
            logger.error(
                "Teleport task failed operation_id=%s error=%s",
                operation_id,
                exc,
                exc_info=exc,
            )

    def respond_to_push(self, operation_id: str, approved: bool) -> None:
        future = self._push_responses.get(operation_id)
        if future is None:
            raise VibeCodeError("Teleport is not waiting for push approval")
        if not future.done():
            future.set_result(approved)

    async def cancel_teleport(self, operation_id: str) -> bool:
        if reservation := self._reserved_operations.pop(operation_id, None):
            self._execution.finish(reservation.execution)
            return True
        task = self._tasks.get(operation_id)
        if task is None:
            return False
        if future := self._push_responses.get(operation_id):
            if not future.done():
                future.set_result(False)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True

    def _fail_early(self, *, stage: TeleportFailureStage, error_class: str) -> None:
        send_teleport_early_failure_telemetry(
            self._agent_loop.telemetry_client,
            stage=stage,
            error_class=error_class,
            nb_session_messages=max(len(self._agent_loop.messages) - 1, 0),
        )

    async def _require_teleport_available(self, prompt: str | None) -> None:
        if not self._agent_loop.config.is_active_model_mistral():
            self._fail_early(stage="ineligible", error_class="TeleportIneligibleError")
            raise VibeCodeAccessError(
                "Teleport requires an active Mistral model. Use /model to switch "
                "to a Mistral model, then try again."
            )

        account = await self._read_account()
        if not account.teleport_eligible:
            self._fail_early(stage="ineligible", error_class="TeleportIneligibleError")
            raise VibeCodeAccessError(self._teleport_access_message(account))

        has_history = any(
            message.role is not Role.system for message in self._agent_loop.messages
        )
        if prompt or has_history:
            return
        self._fail_early(stage="no_history", error_class="TeleportNoHistoryError")
        raise VibeCodeError("No conversation history to teleport.")

    def _teleport_access_message(self, account: AccountView) -> str:
        action = account.teleport_action
        url = (
            action.url
            if action is not None
            else f"{self._agent_loop.base_config.vibe_base_url.rstrip('/')}/code/extensions?focus=key"
        )
        if action is not None and action.kind is AccountActionKind.SWITCH_API_KEY:
            return (
                "Teleport requires a Vibe Pro API key, but the current key is on a "
                f"different plan. Switch to your Vibe Pro API key: {url}"
            )
        return (
            "Teleport requires a Vibe Pro subscription. Your current API key isn't "
            f"eligible. Upgrade to Vibe Pro: {url}"
        )

    async def reset(self) -> None:
        for future in self._push_responses.values():
            if not future.done():
                future.set_result(False)
        self._push_responses.clear()
        tasks = list(self._tasks.values())
        errors = await cancel_tasks(tasks, label="Vibe Code operation")
        self._tasks.clear()
        for reservation in self._reserved_operations.values():
            self._execution.finish(reservation.execution)
        self._reserved_operations.clear()
        async with self._picker_lock:
            self._reset_picker_state()
        if len(errors) == 1:
            raise errors[0]
        if errors:
            raise BaseExceptionGroup("Failed to reset Vibe Code", errors)

    async def close(self) -> None:
        await self.reset()

    def view(self) -> VibeCodePickerView:
        _, data, git = self._require_picker()
        return VibeCodePickerView(
            context=_context(data.context),
            state=_state(data.state),
            git=_git(git),
            saved_project_link_cleared=self._saved_link_cleared,
            project_repo_remote_changed=self._remote_changed,
        )

    async def _run_teleport(
        self,
        params: TeleportStartParams,
        project_picker: ProjectPickerTelemetryPayload,
        execution: ActiveSessionExecution,
    ) -> None:
        response: TeleportPushResponseEvent | None = None
        try:
            async with aclosing(
                self._agent_loop.teleport_to_vibe_code(
                    params.prompt,
                    project_id=params.project_id,
                    project_picker=project_picker,
                )
            ) as generator:
                while True:
                    try:
                        event = await generator.asend(response)
                    except StopAsyncIteration:
                        return
                    response = None
                    public = _teleport_event(params.operation_id, event)
                    if isinstance(event, TeleportCompleteEvent):
                        self._execution.finish(execution)
                    push_response: asyncio.Future[bool] | None = None
                    if isinstance(event, TeleportPushRequiredEvent):
                        push_response = asyncio.get_running_loop().create_future()
                        self._push_responses[params.operation_id] = push_response
                    try:
                        await self._notify(
                            "vibeCode/teleport/event", TeleportEventParams(event=public)
                        )
                    except Exception:
                        self._push_responses.pop(params.operation_id, None)
                        if push_response is not None:
                            push_response.cancel()
                        raise
                    if isinstance(event, TeleportCompleteEvent):
                        return
                    if push_response is not None:
                        approved = await push_response
                        self._push_responses.pop(params.operation_id, None)
                        response = TeleportPushResponseEvent(approved=approved)
        except asyncio.CancelledError:
            raise
        except (TeleportError, ServiceTeleportError) as exc:
            code = (
                "saved_project_stale"
                if is_saved_project_stale_error(str(exc))
                else "teleport_failed"
            )
            self._execution.finish(execution)
            await self._notify(
                "vibeCode/teleport/event",
                TeleportEventParams(
                    event=TeleportFailed(
                        operation_id=params.operation_id,
                        error=PublicError(message=str(exc), code=code),
                    )
                ),
            )
        except Exception as exc:
            logger.exception(
                "Unexpected teleport failure operation_id=%s", params.operation_id
            )
            self._execution.finish(execution)
            await self._notify(
                "vibeCode/teleport/event",
                TeleportEventParams(
                    event=TeleportFailed(
                        operation_id=params.operation_id,
                        error=PublicError(message=str(exc), code="teleport_failed"),
                    )
                ),
            )
        finally:
            self._push_responses.pop(params.operation_id, None)
            self._execution.finish(execution)

    def _make_service(self) -> VibeCodeProjectPickerService:
        config = self._agent_loop.config
        api_key = config.vibe_code_api_key
        if not api_key:
            raise VibeCodeError(f"{config.vibe_code_api_key_env_var} not set.")
        return VibeCodeProjectPickerService(
            base_url=config.vibe_code_sessions_base_url,
            api_key=api_key,
            repo_root=self._agent_loop.cwd,
            project_store=self._store,
            timeout=config.api_timeout,
        )

    async def _read_git(self) -> GitRepoInfo:
        try:
            from vibe.core.teleport.git import GitRepository

            async with GitRepository() as repository:
                return await repository.get_info()
        except ServiceTeleportError as exc:
            raise VibeCodeError(str(exc)) from exc

    def _require_picker(
        self, picker_id: str | None = None
    ) -> tuple[
        VibeCodeProjectPickerService, VibeCodeProjectPickerInitialData, GitRepoInfo
    ]:
        if self._service is None or self._data is None or self._git is None:
            raise VibeCodeError("Vibe Code project picker is not ready")
        if picker_id is not None and picker_id != self._picker_id:
            raise VibeCodeConflictError("Vibe Code project picker is stale")
        return self._service, self._data, self._git

    def _reset_picker_state(self) -> None:
        self._service = None
        self._data = None
        self._git = None
        self._picker_id = None
        self._picker_purpose = None
        self._selected_project_id = None
        self._created_project_ids.clear()
        self._saved_link_cleared = False
        self._remote_changed = False
        self._project_picker = None

    def _picker_telemetry(
        self, *, source: ProjectSelectionSource, shown: bool
    ) -> ProjectPickerTelemetryPayload:
        _, data, _ = self._require_picker()
        return build_project_picker_telemetry(
            source=source,
            shown=shown,
            projects=data.state.projects,
            repo_url=data.context.repo_url,
            saved_project_link_cleared=self._saved_link_cleared,
            project_repo_remote_changed=self._remote_changed,
        )

    def _send_picker_cancelled(self, payload: ProjectPickerTelemetryPayload) -> None:
        self._agent_loop.telemetry_client.send_teleport_failed(
            stage="cancelled",
            error_class="TeleportProjectPickerCancelledError",
            push_required=False,
            nb_session_messages=max(len(self._agent_loop.messages) - 1, 0),
            project_picker=payload,
        )


def _project(project: CoreVibeCodeProject) -> VibeCodeProject:
    return VibeCodeProject(
        project_id=project.project_id,
        name=project.name,
        repositories=[
            VibeCodeRepository(
                repo_url=repository.repo_url, default_branch=repository.default_branch
            )
            for repository in project.repositories
        ],
        is_read_only=project.is_read_only,
    )


def _context(context: ProjectPickerContext) -> VibeCodePickerContext:
    link = context.saved_link
    return VibeCodePickerContext(
        repo_root=str(context.repo_root),
        repo_url=context.repo_url,
        repo_name=context.repo_name,
        saved_link=(
            None
            if link is None
            else VibeCodeProjectLink(
                repo_root=str(link.repo_root),
                repo_url=link.repo_url,
                project_id=link.project_id,
                project_name=link.project_name,
            )
        ),
    )


def _state(state: VibeCodeProjectPickerState) -> VibeCodePickerState:
    return VibeCodePickerState(
        projects=[_project(project) for project in state.projects],
        next_cursor=state.next_cursor,
        repo_url=state.repo_url,
    )


def _git(info: GitRepoInfo) -> VibeCodeGitInfo:
    return VibeCodeGitInfo(
        remote_name=info.remote_name,
        remote_url=info.remote_url,
        repo=info.repo,
        branch=info.branch,
        default_branch=info.default_branch,
    )


def _teleport_event(operation_id: str, event: object) -> TeleportEvent:
    match event:
        case TeleportSummarizingContextEvent():
            return TeleportSummarizingContext(operation_id=operation_id)
        case TeleportCheckingGitEvent():
            return TeleportCheckingGit(operation_id=operation_id)
        case TeleportPushRequiredEvent(
            unpushed_count=count, branch_not_pushed=branch_not_pushed
        ):
            return TeleportPushRequired(
                operation_id=operation_id,
                unpushed_count=count,
                branch_not_pushed=branch_not_pushed,
            )
        case TeleportPushingEvent():
            return TeleportPushing(operation_id=operation_id)
        case TeleportStartingWorkflowEvent():
            return TeleportStartingWorkflow(operation_id=operation_id)
        case TeleportCompleteEvent(url=url):
            return TeleportComplete(operation_id=operation_id, url=url)
        case _:
            raise VibeCodeError(f"Unknown teleport event: {type(event).__name__}")
