from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from vibe.app_server._account import AccountGateway
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._execution import (
    SessionExecution,
    SessionExecutionConflict,
    SessionExecutionKind,
    cancel_tasks,
)
from vibe.app_server._handler import (
    CoreRequestHandler,
    ResumeOrchestration,
    RootLifecycle,
)
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._legacy_session_backend import (
    LegacySessionBackend,
    LegacySessionBackendHost,
)
from vibe.app_server._projection import project_history
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server._root_session import RootSessionCoordinator
from vibe.app_server._runtime import (
    AgentRuntimeFactory,
    RootOpenRequest,
    RuntimeAuthenticationError,
    RuntimeConfigurationError,
    RuntimeInvalidMigrationSourceError,
    RuntimeSessionNotFoundError,
    RuntimeUnfinishedMigrationError,
    close_agent_loop,
)
from vibe.app_server._session_backend_port import (
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedMCPCatalog,
)
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server._session_history import SessionHistory
from vibe.app_server._sessions import SessionRuntime, SessionRuntimeRegistry
from vibe.app_server._tool_io import ClientToolIO
from vibe.app_server._turns import TurnConflictError, TurnController
from vibe.app_server._utils import now_ms, public_error
from vibe.app_server._worktree_effects import WorktreeEffect
from vibe.app_server.connector_catalog import (
    ConnectorCatalogError,
    ConnectorCatalogService,
)
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.app_server.models import (
    JsonPatchOperation,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicNoticeEntry,
    PublicSessionState,
    SessionTitleUpdatedNoticeDetail,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    AgentConfig,
    HistoryEntryAddedParams,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    ServerErrorParams,
    SessionContinueParams,
    SessionOpenParams,
    SessionOptions,
    SessionResumeParams,
    SessionStartParams,
    SessionStartResponse,
    SessionUpdatedParams,
    TurnStartParams,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.git.errors import GitError
from vibe.core.git.worktree import (
    ManagedWorktree,
    PreparedWorktree,
    WorktreeError,
    WorktreeRepository,
)
from vibe.core.git.worktree.naming_model import suggest_worktree_name
from vibe.core.session import last_session_pointer
from vibe.core.session.session_lease import SessionBusyError
from vibe.core.types import SessionTitleUpdatedEvent, WorktreeContext
from vibe.observability.logging import logger

type OpenRoot = Callable[[RootOpenRequest], Awaitable[AgentLoop]]
type StageRoot = Callable[[AgentLoop], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class WorktreeResolution:
    options: SessionOptions
    prepared_worktree: PreparedWorktree | None = None


@dataclass(frozen=True, slots=True)
class OpenedRuntime:
    agent_loop: AgentLoop
    worktree_resolution: WorktreeResolution


class LegacySessionRuntimeController:
    def __init__(
        self,
        *,
        open_root: OpenRoot,
        runtime_factory: AgentRuntimeFactory,
        host_handler: HostRequestHandler,
        stage_root: StageRoot | None,
        services: SessionBackendServices,
        mcp_catalog_service: MCPCatalogService | None = None,
        connector_catalog_service: ConnectorCatalogService | None = None,
        account_gateway: AccountGateway | None = None,
        identity_gateway: IdentityGateway | None = None,
    ) -> None:
        self._open_root = open_root
        self._runtime_factory = runtime_factory
        self._host_handler = host_handler
        self._stage_root = stage_root
        self._services = services
        self._mcp_catalog_service = mcp_catalog_service
        self._connector_catalog_service = connector_catalog_service
        self._account_gateway = account_gateway
        self._identity_gateway = identity_gateway
        self._scheduler_enabled = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self._title_drain_task: asyncio.Task[None] | None = None
        # The agent loop whose out-of-band queue the drain is bound to, so a
        # restart on the same loop doesn't add a second consumer to one queue.
        self._title_drain_loop: AgentLoop | None = None
        self._resume_tasks: set[asyncio.Task[None]] = set()
        self._tasks: set[asyncio.Task[None]] = set()
        self._swept_worktree_buckets: set[str] = set()
        self._root: LegacySessionBackend | None = None
        self._tool_io = ClientToolIO(services)
        self._sessions = SessionRuntimeRegistry(
            services.record_child_notification,
            services.publish_callback,
            services.event_watermark,
            self._tool_io,
            runtime_factory,
        )

    def create_host(self) -> LegacySessionBackendHost:
        return LegacySessionBackendHost(
            start=self.start,
            resume=self.resume,
            continue_latest=self.continue_latest,
            current_session=self.current_session,
            host_handler=self._host_handler,
            stop_background_tasks=self.stop_background_tasks,
            after_lifecycle_response=self._after_lifecycle_response,
        )

    def current_session(self) -> LegacySessionBackend | None:
        return self._root

    async def start(self, params: SessionStartParams) -> LegacySessionBackend:
        self._scheduler_enabled = not params.agent_config.headless
        if root := self._root:
            await root.handler.dispatch(
                "session/start", params.model_dump(mode="json", by_alias=True)
            )
            return root
        opened = await self._open_runtime(params, None)
        await self._attach_opened_runtime(opened, params.history_limit)
        return self._require_root()

    async def resume(
        self, params: SessionResumeParams
    ) -> tuple[LegacySessionBackend, Callable[[], None] | None]:
        self._scheduler_enabled = not params.agent_config.headless
        _reject_worktree_input(params.agent_config)
        if root := self._root:
            result = await root.handler.dispatch(
                "session/resume", params.model_dump(mode="json", by_alias=True)
            )
            return root, result.after_response
        try:
            opened = await self._open_runtime(params, params.session_id)
        except RuntimeSessionNotFoundError as exc:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
            ) from exc
        except RuntimeUnfinishedMigrationError as exc:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                str(exc),
                data={"harnessCode": "unfinished_work_migration"},
            ) from exc
        except RuntimeInvalidMigrationSourceError as exc:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                str(exc),
                data={"harnessCode": "invalid_migration_source"},
            ) from exc
        await self._attach_opened_runtime(opened, params.history_limit, resumed=True)
        return self._require_root(), None

    async def continue_latest(
        self, params: SessionContinueParams
    ) -> LegacySessionBackend:
        self._scheduler_enabled = not params.agent_config.headless
        _reject_worktree_input(params.agent_config)
        if root := self._root:
            await root.handler.dispatch(
                "session/continue", params.model_dump(mode="json", by_alias=True)
            )
            return self._require_root()
        try:
            opened = await self._open_runtime(params, None, continue_latest=True)
        except RuntimeSessionNotFoundError as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        await self._attach_opened_runtime(opened, params.history_limit, resumed=True)
        return self._require_root()

    async def stop_background_tasks(self, current: object) -> list[BaseException]:
        tasks = [task for task in self._tasks if task is not current]
        self._tasks.clear()
        scheduler = self._scheduler_task
        self._scheduler_task = None
        if scheduler is not None and scheduler is not current:
            tasks.append(scheduler)
        drain = self._title_drain_task
        self._title_drain_task = None
        self._title_drain_loop = None
        if drain is not None and drain is not current:
            tasks.append(drain)
        self._resume_tasks.clear()
        return await cancel_tasks(tasks, label="legacy session runtime")

    def _after_lifecycle_response(self) -> None:
        if self._scheduler_enabled:
            self._ensure_scheduler()
        self._restart_title_drain()

    def _require_root(self) -> LegacySessionBackend:
        if self._root is None:
            raise RuntimeError("The active backend has no legacy runtime capability")
        return self._root

    @property
    def _agent_loop(self) -> AgentLoop:
        return self._require_root().session.agent_loop

    @property
    def _resources(self) -> ResourceRequestHandler:
        return self._require_root().resources

    @property
    def _root_session(self) -> RootSessionCoordinator:
        return self._require_root().coordinator

    @property
    def _turns(self) -> TurnController:
        return self._require_root().session.turns

    @property
    def _handler(self) -> CoreRequestHandler:
        return self._require_root().handler

    def _track_task(self, task: asyncio.Task[None]) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(self._services.task_finished)

    def _spawn_resume_task(self, task: asyncio.Task[None]) -> None:
        self._resume_tasks.add(task)
        task.add_done_callback(self._resume_tasks.discard)
        self._track_task(task)

    def _bind_root(
        self,
        agent_loop: AgentLoop,
        mcp_catalog: ResolvedMCPCatalog | None,
        connector_catalog: ResolvedConnectorCatalog | None,
        connector_selection: ResolvedConnectorSelection | None,
    ) -> None:
        execution = SessionExecution()
        history = SessionHistory(project_history(agent_loop))
        resources = ResourceRequestHandler(
            agent_loop,
            execution,
            self._services.notify,
            self._account_gateway,
            current_event_id=self._services.event_watermark,
            identity_gateway=self._identity_gateway,
        )
        coordinator = RootSessionCoordinator(
            agent_loop,
            resources,
            self._sessions,
            self._services.event_watermark,
            history,
        )
        turns: TurnController | None = None

        def snapshot_state() -> PublicSessionState:
            if turns is None:
                raise RuntimeError("Turn controller is not bound")
            callbacks = [
                entry
                for entry in coordinator.all_history(turns.history)
                if isinstance(entry, PublicCallbackEntry)
            ]
            return coordinator.public_state(
                current_history=turns.history,
                callbacks=callbacks,
                turns=turns.turns,
                retrying=turns.retrying,
                history_limit=200,
            )

        turns = TurnController(
            agent_loop,
            self._services.notify,
            self._services.publish_callback,
            execution,
            self._sessions,
            snapshot_state=snapshot_state,
            tool_io=self._tool_io,
            session_coordinator=coordinator,
        )
        session = SessionRuntime(agent_loop, turns, execution, history)
        handler = CoreRequestHandler(
            agent_loop,
            turns,
            execution,
            self._services.notify,
            self._sessions,
            resources,
            coordinator,
            RootLifecycle(
                replace=self._replace_root,
                adopt=self._adopt_root,
                stage=self._stage_root,
            ),
            ResumeOrchestration(
                runtime_factory=self._runtime_factory,
                current_event_id=self._services.event_watermark,
                spawn_resume_task=self._spawn_resume_task,
            ),
        )
        self._sessions.bind_root(session)
        self._root = LegacySessionBackend(
            session,
            resources,
            coordinator,
            handler,
            self._sessions,
            last_session_pointer.record,
            mcp_catalog=mcp_catalog,
            mcp_authorization_provider=(
                self._mcp_catalog_service.authentication
                if self._mcp_catalog_service is not None
                else None
            ),
            mcp_descriptor_cache_root=(
                Path(agent_loop.config.session_logging.save_dir)
                .expanduser()
                .resolve()
                .parent
                / "mcp-descriptors"
                / "legacy"
            ),
            connector_catalog=connector_catalog,
            connector_selection=connector_selection,
        )

    async def _replace_root(
        self, session_id: str, history_limit: int
    ) -> PublicSessionState:
        previous = self._require_root()
        with previous.session.execution.reserve(
            SessionExecutionKind.LIFECYCLE, f"resume:{session_id}"
        ):
            async with self._services.lifecycle_transition():
                for task in list(self._resume_tasks):
                    task.cancel()
                self._resume_tasks.clear()
                agent_loop = previous.session.agent_loop
                try:
                    await self._runtime_factory.resume_root(agent_loop, session_id)
                except RuntimeSessionNotFoundError as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
                    ) from exc
                except RuntimeUnfinishedMigrationError as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.CONFLICT,
                        str(exc),
                        data={"harnessCode": "unfinished_work_migration"},
                    ) from exc
                except RuntimeInvalidMigrationSourceError as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.INTERNAL_ERROR,
                        str(exc),
                        data={"harnessCode": "invalid_migration_source"},
                    ) from exc
                except SessionBusyError as exc:
                    raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
                except RequestFailure:
                    raise
                except Exception as exc:
                    raise RequestFailure(
                        ProtocolErrorCode.INTERNAL_ERROR,
                        f"Failed to resume session {session_id}: {exc}",
                    ) from exc
                try:
                    await previous.session.turns.reset()
                except Exception:
                    logger.exception(
                        "Failed to reset the turn controller while resuming "
                        "session_id=%s",
                        session_id,
                    )
                self._root_session.replace_from_core()
                try:
                    await self._sessions.close_children()
                except Exception:
                    logger.exception(
                        "Failed to close child sessions while resuming session_id=%s",
                        session_id,
                    )
                self._root_session.attach(session_id)
                return self._root_session.append_checkpoint(
                    current_history=[],
                    kind="resume",
                    message="Session resumed",
                    history_limit=history_limit,
                )

    async def _adopt_root(
        self, replacement: AgentLoop, history_limit: int
    ) -> PublicSessionState:
        async with self._services.lifecycle_transition():
            previous = self._require_root()
            return await self._install_root(
                previous, replacement, history_limit, resume_checkpoint=False
            )

    async def _install_root(
        self,
        previous: LegacySessionBackend,
        replacement: AgentLoop,
        history_limit: int,
        *,
        resume_checkpoint: bool,
    ) -> PublicSessionState:
        self._sessions.release_root(previous.session)
        try:
            (
                connector_catalog,
                connector_selection,
            ) = await self._resolve_connector_configuration(replacement)
            self._bind_root(
                replacement,
                await self._resolve_mcp_catalog(replacement),
                connector_catalog,
                connector_selection,
            )
            await self._apply_connector_configuration(
                connector_catalog, connector_selection
            )
        except BaseException:
            self._sessions.bind_root(previous.session)
            self._root = previous
            await close_agent_loop(replacement)
            raise
        replacement_backend = self._require_root()
        try:
            await self._sessions.close_children()
            self._root_session.attach(replacement.session_id)
            replacement.start_initialize_experiments()
        except BaseException:
            with suppress(BaseException):
                await replacement_backend.shutdown()
            # Failed attached replacements must leave the previous root serving.
            self._sessions.bind_root(previous.session)
            self._root = previous
            raise
        try:
            await previous.shutdown()
        except BaseException as exc:
            logger.warning(
                "Failed to close previous root while resuming session_id=%s",
                replacement.session_id,
                exc_info=exc,
            )
        if resume_checkpoint:
            state = self._root_session.append_checkpoint(
                current_history=[],
                kind="resume",
                message="Session resumed",
                history_limit=history_limit,
            )
        else:
            state = self._root_session.public_state(
                current_history=[],
                callbacks=[],
                turns=[],
                retrying=None,
                history_limit=history_limit,
            )
        replacement_backend.adopt_state(state)
        return state

    async def _attach_opened_session(
        self,
        agent_loop: AgentLoop,
        history_limit: int,
        *,
        resumed: bool = False,
        created_worktree: PreparedWorktree | None = None,
    ) -> PublicSessionState:
        try:
            (
                connector_catalog,
                connector_selection,
            ) = await self._resolve_connector_configuration(agent_loop)
            self._bind_root(
                agent_loop,
                await self._resolve_mcp_catalog(agent_loop),
                connector_catalog,
                connector_selection,
            )
            await self._apply_connector_configuration(
                connector_catalog, connector_selection
            )
            if created_worktree is not None:
                await self._report_created_worktree(agent_loop, created_worktree)
            started = await self._handler.dispatch(
                "session/start",
                SessionStartParams(
                    agent_config=AgentConfig(cwd=str(agent_loop.cwd)),
                    history_limit=history_limit,
                ).model_dump(mode="json", by_alias=True),
            )
            assert isinstance(started.response, SessionStartResponse)
            state = started.response.state
            self._schedule_admin_config_fetch()
            if managed := ManagedWorktree.at(agent_loop.cwd):
                managed.hold(agent_loop.session_id)
            self._schedule_worktree_claim_sweep(agent_loop.cwd)
            if resumed:
                state = self._root_session.append_checkpoint(
                    current_history=[],
                    kind="resume",
                    message="Session resumed",
                    history_limit=history_limit,
                )
            self._require_root().adopt_state(state)
            return state
        except BaseException:
            root = self._root
            self._root = None
            if root is not None:
                await root.shutdown()
            else:
                await close_agent_loop(agent_loop)
            raise

    async def _apply_connector_configuration(
        self,
        catalog: ResolvedConnectorCatalog | None,
        selection: ResolvedConnectorSelection | None,
    ) -> None:
        if catalog is None or selection is None:
            return
        backend = self._require_root()
        if backend.session.agent_loop.connector_registry is None:
            return
        await backend.reconfigure_connectors(catalog, selection, force=False)

    async def _resolve_mcp_catalog(
        self, agent_loop: AgentLoop
    ) -> ResolvedMCPCatalog | None:
        if self._mcp_catalog_service is None:
            return None
        return await self._mcp_catalog_service.resolve_catalog(
            agent_loop.config_orchestrator
        )

    async def _resolve_connector_configuration(
        self, agent_loop: AgentLoop
    ) -> tuple[ResolvedConnectorCatalog | None, ResolvedConnectorSelection | None]:
        service = self._connector_catalog_service
        if service is None:
            return None, None
        try:
            catalog = await service.resolve_catalog(agent_loop.config_orchestrator)
        except ConnectorCatalogError:
            logger.warning("Connector catalog is unavailable while binding the session")
            catalog = None
        selection = service.resolve_selection(agent_loop.config_orchestrator, catalog)
        return catalog, selection

    def _schedule_admin_config_fetch(self) -> None:
        task = asyncio.create_task(
            self._fetch_admin_config(), name="vibe-admin-config-fetch"
        )
        self._track_task(task)

    def _schedule_worktree_claim_sweep(self, cwd: Path) -> None:
        task = asyncio.create_task(
            self._sweep_worktree_claims(cwd), name="vibe-worktree-claim-sweep"
        )
        self._track_task(task)

    async def _sweep_worktree_claims(self, cwd: Path) -> None:
        try:
            bucket = await asyncio.to_thread(WorktreeRepository.bucket_for, cwd)
            if bucket is None or bucket in self._swept_worktree_buckets:
                return
            self._swept_worktree_buckets.add(bucket)
            await asyncio.to_thread(WorktreeRepository.sweep_claims, cwd)
        except Exception as exc:
            logger.debug("Worktree claim sweep failed", exc_info=exc)

    async def _fetch_admin_config(self) -> None:
        if self._root is None:
            return
        try:
            changed = await self._resources.apply_admin_config()
        except Exception as exc:
            logger.debug("Admin config fetch failed", exc_info=exc)
            return
        if changed and self._root is not None:
            await self._services.notify(
                "runtime/updated",
                RuntimeUpdatedParams(
                    session_id=self._agent_loop.session_id,
                    runtime=self._resources.runtime_snapshot(),
                ),
            )

    async def _open_runtime(
        self,
        params: SessionOpenParams,
        session_id: str | None,
        *,
        continue_latest: bool = False,
    ) -> OpenedRuntime:
        options = params.agent_config.model_copy(update={"cwd": params.cwd})
        try:
            worktree_resolution = await self._resolve_worktree(options)
            try:
                agent_loop = await self._open_root(
                    RootOpenRequest(
                        options=worktree_resolution.options,
                        client_info=self._services.client_info(),
                        client_capabilities=self._services.client_capabilities(),
                        session_id=session_id,
                        continue_latest=continue_latest,
                    )
                )
            except BaseException:
                await self._cleanup_worktree(worktree_resolution)
                raise
            return OpenedRuntime(
                agent_loop=agent_loop, worktree_resolution=worktree_resolution
            )
        except RuntimeAuthenticationError as exc:
            raise RequestFailure(
                ProtocolErrorCode.UNAUTHORIZED,
                str(exc),
                data={"provider": exc.provider},
            ) from exc
        except RuntimeConfigurationError as exc:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                str(exc),
                data={"kind": "configuration"},
            ) from exc
        except GitError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

    async def _resolve_worktree(self, options: SessionOptions) -> WorktreeResolution:
        if options.worktree is None:
            return WorktreeResolution(options=options)
        suggested_name = await self._suggest_worktree_name(options)
        resolve = asyncio.create_task(
            asyncio.to_thread(resolve_worktree, options, suggested_name)
        )
        try:
            return await asyncio.shield(resolve)
        except asyncio.CancelledError:
            with suppress(BaseException):
                await self._cleanup_worktree(await resolve)
            raise

    @staticmethod
    async def _suggest_worktree_name(options: SessionOptions) -> str | None:
        worktree = options.worktree
        if worktree is None or worktree.kind != "auto":
            return None
        return await suggest_worktree_name(
            worktree.prompt, cwd=Path(options.cwd or Path.cwd())
        )

    async def _attach_opened_runtime(
        self, opened: OpenedRuntime, history_limit: int, *, resumed: bool = False
    ) -> PublicSessionState:
        created = opened.worktree_resolution.prepared_worktree
        if created is not None and not created.created:
            created = None
        try:
            state = await self._attach_opened_session(
                opened.agent_loop,
                history_limit,
                resumed=resumed,
                created_worktree=created,
            )
        except BaseException:
            await self._cleanup_worktree(opened.worktree_resolution)
            raise
        root = self._root
        if root is not None and created is not None:
            root.created_worktree = created
        return state

    async def _report_created_worktree(
        self, agent_loop: AgentLoop, worktree: PreparedWorktree
    ) -> None:
        entry_id = f"worktree-{worktree.name}"
        effect = WorktreeEffect.created(worktree)
        try:
            await self._turns.start_effect(
                session_id=agent_loop.session_id,
                entry_id=entry_id,
                title="worktree",
                detail=effect.detail,
            )
            await self._turns.complete_effect(entry_id, effect.state)
        except Exception as exc:
            logger.warning("Failed to show the created worktree", exc_info=exc)
        try:
            await agent_loop.session_logger.persist_created_worktree(
                WorktreeContext(
                    entry_id=entry_id,
                    name=worktree.name,
                    branch=worktree.branch,
                    path=str(worktree.root),
                    created_at=now_ms(),
                )
            )
        except Exception as exc:
            logger.warning("Failed to record the created worktree", exc_info=exc)

    async def _cleanup_worktree(self, worktree_resolution: WorktreeResolution) -> None:
        worktree = worktree_resolution.prepared_worktree
        if worktree is None or not worktree.created:
            return
        try:
            await asyncio.to_thread(
                worktree.remove, delete_branch=worktree.branch_created
            )
        except Exception as exc:
            logger.warning(
                "Failed to clean up worktree after session startup failure",
                exc_info=exc,
            )
            return
        if managed := ManagedWorktree.at(worktree.root):
            managed.forget()

    def _ensure_scheduler(self) -> None:
        if self._scheduler_task is not None and not self._scheduler_task.done():
            return
        self._scheduler_task = asyncio.create_task(
            self._run_scheduler(), name="vibe-scheduled-loops"
        )
        self._scheduler_task.add_done_callback(self._scheduler_finished)

    @staticmethod
    def _scheduler_finished(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Scheduled loops task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _restart_title_drain(self) -> None:
        # Rebind to the current agent loop; resume/continue swaps it in place.
        # If we're already draining this loop's queue, do nothing: cancelling and
        # immediately recreating would briefly leave two consumers on the one
        # queue, and a title event could be delivered to the dying task and lost.
        loop = self._agent_loop
        task = self._title_drain_task
        if task is not None and not task.done() and self._title_drain_loop is loop:
            return
        if task is not None and not task.done():
            task.cancel()
        self._title_drain_loop = loop
        self._title_drain_task = asyncio.create_task(
            self._run_title_drain(), name="vibe-title-drain"
        )
        self._title_drain_task.add_done_callback(self._title_drain_finished)

    @staticmethod
    def _title_drain_finished(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Session title drain task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _run_title_drain(self) -> None:
        async for event in self._agent_loop.out_of_band_events():
            try:
                if isinstance(event, SessionTitleUpdatedEvent):
                    await self._notify_title(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._services.notify(
                    "error", ServerErrorParams(error=public_error(exc))
                )

    async def _notify_title(self, event: SessionTitleUpdatedEvent) -> None:
        # The background title is session metadata: reflect it in the public
        # projection (a single /title patch) so every projection consumer, e.g.
        # the ACP session state, sees it live. The terminal notice is the
        # separate, CLI-owned concern for the tab title. Both are emitted by this
        # single drain owner, so nothing competes with the turn projector.
        session_id = event.session_id
        # A reset (/new, /clear) can swap in a new session while a title for the
        # old one is still queued. Dropping the stale event keeps its title from
        # reviving on the fresh session's tab.
        if session_id != self._agent_loop.session_id:
            return
        title = event.title
        now = now_ms()
        await self._services.notify(
            "session/updated",
            SessionUpdatedParams(
                event_id=0,
                session_id=session_id,
                patch=[JsonPatchOperation(op="replace", path="/title", value=title)],
                emitted_at=now,
            ),
        )
        entry = PublicNoticeEntry(
            id=str(uuid4()),
            session_id=session_id,
            turn_id=None,
            created_at=now,
            updated_at=now,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            level="info",
            message="Session title updated",
            detail=SessionTitleUpdatedNoticeDetail(title=title),
        )
        await self._services.notify(
            "history/entryAdded",
            HistoryEntryAddedParams(
                event_id=0,
                session_id=session_id,
                turn_id=None,
                entry=entry,
                emitted_at=now,
            ),
        )

    async def _run_scheduler(self) -> None:
        while True:
            try:
                delay = self._resources.next_loop_due_in()
                await asyncio.sleep(max(0.05, min(delay, 1.0)))
                if self._require_root().session.execution.active is not None:
                    continue
                loop = self._resources.due_loop()
                if loop is None:
                    continue
                try:
                    _, start = self._turns.start(
                        TurnStartParams(
                            session_id=self._agent_loop.session_id,
                            message=[TextContentBlock(text=loop.prompt)],
                        ),
                        scheduled_loop_id=loop.id,
                    )
                except (SessionExecutionConflict, TurnConflictError):
                    continue
                start()
                await self._resources.mark_loop_fired(loop.id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await self._services.notify(
                    "error", ServerErrorParams(error=public_error(exc))
                )


def _reject_worktree_input(options: SessionOptions) -> None:
    if options.worktree is None:
        return
    raise RequestFailure(
        ProtocolErrorCode.INVALID_PARAMS,
        "worktree is only supported when starting a session",
    )


def create_legacy_session_backend_host(
    *,
    open_root: OpenRoot,
    runtime_factory: AgentRuntimeFactory,
    host_handler: HostRequestHandler,
    stage_root: StageRoot | None,
    services: SessionBackendServices,
    mcp_catalog_service: MCPCatalogService | None = None,
    connector_catalog_service: ConnectorCatalogService | None = None,
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
) -> LegacySessionBackendHost:
    return LegacySessionRuntimeController(
        open_root=open_root,
        runtime_factory=runtime_factory,
        host_handler=host_handler,
        stage_root=stage_root,
        services=services,
        mcp_catalog_service=mcp_catalog_service,
        connector_catalog_service=connector_catalog_service,
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
    ).create_host()


def resolve_worktree(
    options: SessionOptions, suggested_name: str | None = None
) -> WorktreeResolution:
    requested_worktree = options.worktree
    if requested_worktree is None:
        return WorktreeResolution(options=options)

    base_cwd = Path(options.cwd or Path.cwd()).expanduser().resolve()
    if not base_cwd.is_dir():
        raise WorktreeError(f"Local project path is not a directory: {base_cwd}")

    prepared_worktree: PreparedWorktree | None = None
    match requested_worktree.kind:
        case "existing":
            requested = Path(requested_worktree.cwd).expanduser().resolve()
            with WorktreeRepository.open(base_cwd) as repository:
                linked = repository.linked()
            if not any(worktree.path == requested for worktree in linked):
                raise WorktreeError(
                    f"Worktree is not linked to the local project: {requested}"
                )
            cwd = requested
        case "create":
            with WorktreeRepository.open(base_cwd) as repository:
                created = repository.prepare(
                    requested_worktree.name, branch=requested_worktree.branch
                )
            prepared_worktree = created
            cwd = created.path
        case "auto":
            with WorktreeRepository.open(base_cwd) as repository:
                created = repository.prepare_auto(
                    prompt=requested_worktree.prompt, suggested_name=suggested_name
                )
            prepared_worktree = created
            cwd = created.path
        case _:
            raise TypeError(f"Unsupported worktree input: {requested_worktree!r}")

    return WorktreeResolution(
        options=options.model_copy(
            update={"cwd": str(cwd), "workspace_roots": [str(cwd)], "worktree": None}
        ),
        prepared_worktree=prepared_worktree,
    )
