from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._project_links import (
    ProjectLinksAuthError,
    ProjectLinksController,
    ProjectLinksError,
    ProjectLinksInternalError,
    ProjectLinksInvalidRequest,
)
from vibe.app_server._projection import project_config_view, project_message_history
from vibe.app_server._state import build_stored_public_state, history_page
from vibe.app_server._utils import now_ms
from vibe.app_server._workspace import (
    WorkspaceTrustError,
    decide_workspace_trust,
    read_untrusted_config_dirs,
    read_workspace_trust,
)
from vibe.app_server.models import IdleSessionStatus, PublicSession
from vibe.app_server.protocol import (
    ConfigReadParams,
    ConfigReadResponse,
    ConfigSchemaReadParams,
    ConfigSchemaReadResponse,
    EmptyResponse,
    ProjectLinkMutationResponse,
    ProjectLinksCreateParams,
    ProjectLinksInspectRootParams,
    ProjectLinksInspectRootResponse,
    ProjectLinksLinkParams,
    ProjectLinksListParams,
    ProjectLinksListResponse,
    ProjectLinksPickerLoadMoreParams,
    ProjectLinksPickerLoadMoreResponse,
    ProjectLinksPickerLoadParams,
    ProjectLinksPickerLoadResponse,
    ProjectLinksResolveRootParams,
    ProjectLinksResolveRootResponse,
    ProjectLinksSaveParams,
    ProjectLinksUnlinkParams,
    ProjectLinksUnlinkResponse,
    ProtocolErrorCode,
    SessionDeleteParams,
    SessionHistoryGetParams,
    SessionHistoryGetResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionListParams,
    SessionListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceLinkedWorktree,
    WorkspaceTrustDecisionParams,
    WorkspaceTrustStatusParams,
    WorkspaceUntrustedConfigParams,
    WorkspaceWorktreeListParams,
    WorkspaceWorktreeListResponse,
    WorkspaceWorktreeRemoveParams,
    WorkspaceWorktreeRemoveResponse,
    WorktreeRemoveOutcome,
)
from vibe.core.config import VibeConfigSchema, build_default_orchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.git.errors import (
    GitError,
    GitRepositoryNotFoundError,
    GitUnavailableError,
)
from vibe.core.git.worktree import (
    ManagedWorktree,
    WorktreeReleaseOutcome,
    WorktreeRepository,
)
from vibe.core.hooks.config import load_hooks_from_fs
from vibe.core.session import last_session_pointer
from vibe.core.session.resume_sessions import (
    ResumeSessionInfo,
    list_local_resume_sessions,
)
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_title,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import SkillSource
from vibe.core.types import LLMMessage, SessionMetadata
from vibe.observability.logging import logger

_HOST_METHODS = frozenset({
    "config/read",
    "config/schema",
    "projectLinks/create",
    "projectLinks/inspectRoot",
    "projectLinks/link",
    "projectLinks/list",
    "projectLinks/picker/load",
    "projectLinks/picker/loadMore",
    "projectLinks/resolveRoot",
    "projectLinks/save",
    "projectLinks/unlink",
    "session/delete",
    "session/history/list",
    "session/list",
    "session/read",
    "session/rename",
    "workspace/trust/decision",
    "workspace/trust/untrustedConfig",
    "workspace/trust/status",
    "workspace/worktrees/list",
    "workspace/worktrees/remove",
})


class HostRequestHandler:
    def __init__(self, harness_files: HarnessFilesManager) -> None:
        self._harness_files = harness_files
        self._project_links = ProjectLinksController()

    def handles(self, method: str) -> bool:
        return method in _HOST_METHODS

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        try:
            response = await self._dispatch(method, raw_params)
        except WorkspaceTrustError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except GitError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except ProjectLinksAuthError as exc:
            raise RequestFailure(ProtocolErrorCode.UNAUTHORIZED, str(exc)) from exc
        except ProjectLinksInvalidRequest as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except ProjectLinksInternalError as exc:
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
        except ProjectLinksError as exc:
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
        except FileNotFoundError as exc:
            raise RequestFailure(ProtocolErrorCode.NOT_FOUND, str(exc)) from exc
        return DispatchResult(response)

    async def _dispatch(self, method: str, raw_params: dict[str, Any]) -> ProtocolModel:
        match method:
            case "config/schema":
                validate_wire(ConfigSchemaReadParams, raw_params)
                response: ProtocolModel = config_schema_response()
            case "config/read":
                response = await self._read_config(
                    validate_wire(ConfigReadParams, raw_params)
                )
            case "session/list":
                params = validate_wire(SessionListParams, raw_params)
                config = await self._load_config(params.cwd)
                response = await asyncio.to_thread(project_session_list, config, params)
            case "session/read":
                params = validate_wire(SessionReadParams, raw_params)
                config = await self._load_config(None)
                response = await asyncio.to_thread(self._read_session, params, config)
            case "session/delete":
                params = validate_wire(SessionDeleteParams, raw_params)
                config = await self._load_config(None)
                await delete_saved_session(params.session_id, config.session_logging)
                response = EmptyResponse()
            case "session/rename":
                params = validate_wire(SessionTitleUpdateParams, raw_params)
                config = await self._load_config(None)
                try:
                    metadata = await update_saved_session_title(
                        params.session_id, params.title, config.session_logging
                    )
                except ValueError as exc:
                    if str(exc).startswith("Session not found:"):
                        raise FileNotFoundError(str(exc)) from exc
                    raise RequestFailure(
                        ProtocolErrorCode.INVALID_PARAMS, str(exc)
                    ) from exc
                title = metadata.get("title")
                if not isinstance(title, str):
                    raise RuntimeError("The saved session title was not updated")
                updated_at = metadata.get("end_time")
                response = SessionTitleUpdateResponse(
                    title=title,
                    updated_at=updated_at if isinstance(updated_at, str) else None,
                )
            case "session/history/list":
                params = validate_wire(SessionHistoryListParams, raw_params)
                config = await self._load_config(None)
                response = await asyncio.to_thread(self._history_list, params, config)
            case "session/history/get":
                params = validate_wire(SessionHistoryGetParams, raw_params)
                config = await self._load_config(None)
                response = await asyncio.to_thread(
                    self._get_session_history, params, config
                )
            case _ if method.startswith("workspace/"):
                response = await self._dispatch_workspace(method, raw_params)
            case _ if method.startswith("projectLinks/"):
                response = await self._dispatch_project_links(method, raw_params)
            case _:
                raise method_not_found(method)
        return response

    async def _read_config(self, params: ConfigReadParams) -> ConfigReadResponse:
        if params.session_id is not None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {params.session_id}"
            )
        orchestrator = await self._load_orchestrator(params.cwd)
        config = orchestrator.config
        view = project_config_view(
            config, active_model_pinned=bool(orchestrator.persisted_active_model())
        )
        session_files = self._harness_files.for_session(self._cwd(params.cwd))

        skill_mgr = SkillManager(
            config_getter=lambda: config, harness_files=session_files
        )
        skills_count = sum(
            1
            for skill in skill_mgr.available_skills.values()
            if skill.source is not SkillSource.BUILTIN
        )
        hooks_count = len(load_hooks_from_fs(harness_files=session_files).hooks)
        mcp_servers_total = len(config.mcp_servers)
        mcp_servers_enabled = sum(
            1 for server in config.mcp_servers if not server.disabled
        )

        return ConfigReadResponse(
            config=view,
            skills_count=skills_count,
            hooks_count=hooks_count,
            mcp_servers_total=mcp_servers_total,
            mcp_servers_enabled=mcp_servers_enabled,
        )

    async def _dispatch_project_links(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        match method:
            case "projectLinks/list":
                validate_wire(ProjectLinksListParams, raw_params)
                response: ProtocolModel = ProjectLinksListResponse.model_validate(
                    await self._project_links.list_links()
                )
            case "projectLinks/resolveRoot":
                params = validate_wire(ProjectLinksResolveRootParams, raw_params)
                response = ProjectLinksResolveRootResponse.model_validate(
                    await self._project_links.resolve_root(params.root_path)
                )
            case "projectLinks/inspectRoot":
                params = validate_wire(ProjectLinksInspectRootParams, raw_params)
                response = ProjectLinksInspectRootResponse.model_validate(
                    await self._project_links.inspect_root(params.root_path)
                )
            case "projectLinks/picker/load":
                params = validate_wire(ProjectLinksPickerLoadParams, raw_params)
                response = ProjectLinksPickerLoadResponse.model_validate(
                    await self._project_links.picker_load(params.root_path)
                )
            case "projectLinks/picker/loadMore":
                params = validate_wire(ProjectLinksPickerLoadMoreParams, raw_params)
                response = ProjectLinksPickerLoadMoreResponse.model_validate(
                    await self._project_links.picker_load_more(
                        params.root_path, params.cursor
                    )
                )
            case "projectLinks/create":
                params = validate_wire(ProjectLinksCreateParams, raw_params)
                response = ProjectLinkMutationResponse.model_validate(
                    await self._project_links.create(
                        params.root_path, params.name, params.default_branch
                    )
                )
            case "projectLinks/link":
                params = validate_wire(ProjectLinksLinkParams, raw_params)
                response = ProjectLinkMutationResponse.model_validate(
                    await self._project_links.link(
                        params.root_path, params.project_id, params.project_name
                    )
                )
            case "projectLinks/save":
                params = validate_wire(ProjectLinksSaveParams, raw_params)
                response = ProjectLinkMutationResponse.model_validate(
                    await self._project_links.save(
                        params.root_path,
                        params.project_id,
                        params.project_name,
                        params.expected_repo_url,
                    )
                )
            case "projectLinks/unlink":
                params = validate_wire(ProjectLinksUnlinkParams, raw_params)
                response = ProjectLinksUnlinkResponse.model_validate(
                    await self._project_links.unlink(params.root_path)
                )
            case _:
                raise method_not_found(method)
        return response

    async def _dispatch_workspace(
        self, method: str, raw_params: dict[str, Any]
    ) -> ProtocolModel:
        match method:
            case "workspace/trust/status":
                params = validate_wire(WorkspaceTrustStatusParams, raw_params)
                response: ProtocolModel = await asyncio.to_thread(
                    read_workspace_trust,
                    self._cwd(params.cwd),
                    self._harness_files.trust_store,
                )
            case "workspace/trust/decision":
                params = validate_wire(WorkspaceTrustDecisionParams, raw_params)
                if params.session_id is not None:
                    raise RequestFailure(
                        ProtocolErrorCode.NOT_FOUND,
                        f"Session not found: {params.session_id}",
                    )
                response = await asyncio.to_thread(
                    decide_workspace_trust,
                    self._cwd(params.cwd),
                    params.decision,
                    self._harness_files.trust_store,
                )
            case "workspace/trust/untrustedConfig":
                params = validate_wire(WorkspaceUntrustedConfigParams, raw_params)
                response = await asyncio.to_thread(
                    read_untrusted_config_dirs,
                    self._cwd(params.cwd),
                    self._harness_files.trust_store,
                )
            case "workspace/worktrees/list":
                params = validate_wire(WorkspaceWorktreeListParams, raw_params)
                response = await asyncio.to_thread(
                    worktree_list_response, self._cwd(params.cwd)
                )
            case "workspace/worktrees/remove":
                params = validate_wire(WorkspaceWorktreeRemoveParams, raw_params)
                response = await asyncio.to_thread(
                    worktree_remove_response, self._cwd(params.cwd)
                )
            case _:
                raise method_not_found(method)
        return response

    def _read_session(
        self, params: SessionReadParams, config: VibeConfigSchema
    ) -> SessionReadResponse:
        messages, metadata = self._load_session(params.session_id, config)
        public = build_stored_public_state(
            params.session_id,
            messages,
            metadata,
            history_limit=params.history_limit,
            turns_limit=params.turns_limit,
            include_history=params.include_history,
            include_turns=params.include_turns,
        )
        return SessionReadResponse(state=public, last_event_id=public.event_id)

    def _history_list(
        self, params: SessionHistoryListParams, config: VibeConfigSchema
    ) -> SessionHistoryListResponse:
        messages, metadata = self._load_session(params.session_id, config)
        all_history = project_message_history(params.session_id, messages, metadata)
        page = history_page(
            all_history,
            turn_id=params.turn_id,
            before=params.cursor if params.sort_direction == "backward" else None,
            after=params.cursor if params.sort_direction == "forward" else None,
            limit=params.limit,
        )
        return SessionHistoryListResponse(
            items=page.entries,
            next_cursor=(
                page.cursor.before
                if params.sort_direction == "backward"
                else page.cursor.after
            ),
            previous_cursor=(
                page.cursor.after
                if params.sort_direction == "backward"
                else page.cursor.before
            ),
        )

    def _get_session_history(
        self, params: SessionHistoryGetParams, config: VibeConfigSchema
    ) -> SessionHistoryGetResponse:
        messages, metadata = self._load_session(params.session_id, config)
        # Project the full transcript then take the tail: one history entry can
        # span multiple messages (e.g. assistant + tool results), so pre-slicing
        # raw messages would give wrong entry counts. The limit is capped at 500
        # in the protocol, keeping this O(n) scan bounded.
        history = project_message_history(params.session_id, messages, metadata)
        limit = params.history_limit
        return SessionHistoryGetResponse(history=history[-limit:])

    def _load_session(
        self, session_id: str, config: VibeConfigSchema
    ) -> tuple[list[LLMMessage], SessionMetadata]:
        session_path = SessionLoader.find_session_by_id(
            session_id, config.session_logging
        )
        if session_path is None:
            raise FileNotFoundError(f"Session not found: {session_id}")
        messages, raw_metadata = SessionLoader.load_session(session_path)
        return messages, SessionMetadata.model_validate(raw_metadata)

    async def _load_config(self, cwd: str | None) -> VibeConfigSchema:
        return (await self._load_orchestrator(cwd)).config

    async def _load_orchestrator(
        self, cwd: str | None
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        session_files = self._harness_files.for_session(self._cwd(cwd))
        return await build_default_orchestrator(
            harness_files=session_files, require_api_key=False
        )

    @staticmethod
    def _cwd(value: str | None) -> Path:
        return Path(value or Path.cwd()).expanduser().resolve()


@lru_cache(maxsize=1)
def config_schema_response() -> ConfigSchemaReadResponse:
    schema = VibeConfigSchema.model_json_schema(mode="serialization", by_alias=True)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    version = hashlib.sha256(encoded, usedforsecurity=False).hexdigest()
    return ConfigSchemaReadResponse.model_validate({
        "config_schema_version": f"sha256:{version}",
        "config_schema": schema,
    })


def project_session_list(
    config: VibeConfigSchema, params: SessionListParams
) -> SessionListResponse:
    sessions = list_local_resume_sessions(config, params.cwd)
    roots = _session_roots(sessions)
    filtered = [
        session
        for session in sessions
        if (
            params.root_session_id is None
            or roots[session.session_id] == params.root_session_id
        )
        and (
            params.parent_session_id is None
            or session.parent_session_id == params.parent_session_id
        )
    ]
    filtered.sort(
        key=lambda session: (session.updated_at, session.session_id), reverse=True
    )
    start = _session_cursor_index(filtered, params.cursor)
    page = filtered[start : start + params.limit]
    return SessionListResponse(
        items=[
            PublicSession(
                id=session.session_id,
                root_session_id=roots[session.session_id],
                parent_session_id=session.parent_session_id,
                title=session.title,
                preview=SessionLoader.get_first_user_message(
                    session.session_id, config.session_logging
                ),
                status=IdleSessionStatus(),
                created_at=_time_ms(session.start_time or session.updated_at),
                updated_at=_time_ms(session.updated_at),
                cwd=session.cwd or None,
            )
            for session in page
        ],
        next_cursor=(
            _encode_session_cursor(page[-1])
            if start + len(page) < len(filtered) and page
            else None
        ),
        continue_session_id=_continue_session_id(config, filtered),
    )


def _continue_session_id(
    config: VibeConfigSchema, filtered: list[ResumeSessionInfo]
) -> str | None:
    """The session ``--continue`` resumes: pointer-first, else most recent."""
    if not filtered:
        return None
    candidate_ids = {session.session_id for session in filtered}
    pointer_id = last_session_pointer.load(config.session_logging)
    if pointer_id is not None and pointer_id in candidate_ids:
        return pointer_id
    return filtered[0].session_id


def _session_roots(sessions: list[ResumeSessionInfo]) -> dict[str, str]:
    parents = {session.session_id: session.parent_session_id for session in sessions}
    roots: dict[str, str] = {}
    for session_id in parents:
        seen: set[str] = set()
        current = session_id
        while (parent := parents.get(current)) is not None and parent in parents:
            if parent in seen:
                current = session_id
                break
            seen.add(current)
            current = parent
        roots[session_id] = current
    return roots


def _encode_session_cursor(session: ResumeSessionInfo) -> str:
    payload = f"{session.updated_at}\0{session.session_id}".encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _session_cursor_index(sessions: list[ResumeSessionInfo], cursor: str | None) -> int:
    if cursor is None:
        return 0
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        updated_at, session_id = (
            base64.urlsafe_b64decode(padded).decode().split("\0", 1)
        )
    except (ValueError, UnicodeDecodeError):
        return len(sessions)
    return next(
        (
            index + 1
            for index, session in enumerate(sessions)
            if (session.updated_at, session.session_id) == (updated_at, session_id)
        ),
        len(sessions),
    )


def _time_ms(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return now_ms()


def worktree_list_response(cwd: Path) -> WorkspaceWorktreeListResponse:
    try:
        with WorktreeRepository.open(cwd) as repository:
            worktrees = repository.linked()
    except GitRepositoryNotFoundError:
        logger.debug("Skipping worktree listing for non-git path=%s", cwd)
        worktrees = ()
    except GitUnavailableError:
        # app-server is expected to run without git; a host that cannot list
        # worktrees simply has none, so callers should not see invalid_params.
        logger.debug("Skipping worktree listing without git cwd=%s", cwd)
        worktrees = ()

    return WorkspaceWorktreeListResponse(
        worktrees=[
            WorkspaceLinkedWorktree(
                name=worktree.name,
                branch=worktree.branch,
                cwd=str(worktree.path),
                root=str(worktree.root),
                repo_root=str(worktree.repo_root),
            )
            for worktree in worktrees
        ]
    )


# The wire vocabulary is spelled out in protocol.py so the app-server clients
# do not depend on core, which leaves this as the one place the two meet.
_WORKTREE_REMOVE_OUTCOMES: dict[WorktreeReleaseOutcome, WorktreeRemoveOutcome] = {
    WorktreeReleaseOutcome.REMOVED: "removed",
    WorktreeReleaseOutcome.KEPT_DIRTY: "kept_dirty",
    WorktreeReleaseOutcome.KEPT_IN_USE: "kept_in_use",
    WorktreeReleaseOutcome.KEPT_UNMANAGED: "kept_unmanaged",
    WorktreeReleaseOutcome.NOT_FOUND: "not_found",
}


def worktree_remove_response(cwd: Path) -> WorkspaceWorktreeRemoveResponse:
    managed = ManagedWorktree.at(cwd)
    if managed is None:
        return WorkspaceWorktreeRemoveResponse(outcome="kept_unmanaged")
    try:
        release = managed.release()
    except (GitError, OSError) as e:
        # Keeping a worktree is never a fault the caller can act on, and a
        # failed removal leaves the work intact, so report it as kept.
        logger.warning("Failed to remove worktree cwd=%s: %s", cwd, e)
        return WorkspaceWorktreeRemoveResponse(outcome="kept_error", reasons=[str(e)])

    return WorkspaceWorktreeRemoveResponse(
        outcome=_WORKTREE_REMOVE_OUTCOMES[release.outcome],
        root=None if release.root is None else str(release.root),
        branch=release.branch,
        branch_deleted=release.branch_deleted,
        reasons=list(release.reasons),
    )
