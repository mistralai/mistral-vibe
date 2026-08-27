from __future__ import annotations

import asyncio
import base64
from collections.abc import Sequence
from contextlib import ExitStack
from dataclasses import dataclass
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
    SessionRelocateParams,
    SessionRelocateResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceGitBranchChanges,
    WorkspaceGitCheckout,
    WorkspaceGitCheckoutsParams,
    WorkspaceGitCheckoutsResponse,
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
from vibe.core.git.repo import GitRepo
from vibe.core.git.worktree import (
    LinkedWorktree,
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
    relocate_saved_session,
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
    # Only reached with no session attached. A live one is routed to the agent
    # loop, which moves the workspace it is holding; here there is nothing
    # holding it, so the move is the record and nothing else.
    "session/relocate",
    "session/rename",
    "workspace/trust/decision",
    "workspace/trust/untrustedConfig",
    "workspace/trust/status",
    "workspace/git/checkouts",
    "workspace/git/worktrees/list",
    "workspace/git/worktrees/remove",
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
            case "session/relocate":
                response = await self._relocate_session(
                    validate_wire(SessionRelocateParams, raw_params),
                    await self._load_config(None),
                )
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
            case "workspace/git/worktrees/list":
                params = validate_wire(WorkspaceWorktreeListParams, raw_params)
                response = await asyncio.to_thread(
                    worktree_list_response,
                    self._cwd(params.cwd),
                    params.include_details,
                )
            case "workspace/git/checkouts":
                checkouts = validate_wire(WorkspaceGitCheckoutsParams, raw_params)
                response = await asyncio.to_thread(
                    git_checkouts_response,
                    checkouts.repo_local_paths,
                    Path(checkouts.session_cwd) if checkouts.session_cwd else None,
                )
            case "workspace/git/worktrees/remove":
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

    async def _relocate_session(
        self, params: SessionRelocateParams, config: VibeConfigSchema
    ) -> SessionRelocateResponse:
        """Move a session no agent is holding.

        The live path re-roots trust, harness files and the config layer around
        a running loop. None of that exists here, and none of it has to: those
        are derived from the record when the session is next opened. So the
        move is the record, and the answer is the session read back from it.
        """
        _, metadata = await asyncio.to_thread(
            self._load_session, params.session_id, config
        )
        current = (metadata.environment or {}).get("working_directory")
        if not current:
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Session has no working directory: {params.session_id}",
            )

        target = Path(params.cwd).expanduser().resolve()
        if not target.is_dir():
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS, f"Not a directory: {target}"
            )
        # The same boundary the live move keeps: a session may only reach the
        # counterparts of where it already sits, so a move never grants a tree
        # the session never occupied.
        if not await asyncio.to_thread(_is_counterpart, Path(current), target):
            raise RequestFailure(
                ProtocolErrorCode.INVALID_PARAMS,
                f"Not a worktree of this session's repository: {target}",
            )

        await relocate_saved_session(params.session_id, target, config.session_logging)
        # Read back through the same path `session/read` uses, so the state a
        # move answers with is the state a read of the moved session gives.
        read = await asyncio.to_thread(
            self._read_session, SessionReadParams(session_id=params.session_id), config
        )
        return SessionRelocateResponse(state=read.state)

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


def worktree_list_response(
    cwd: Path, include_details: bool = False
) -> WorkspaceWorktreeListResponse:
    repository_cwd: Path | None = None
    try:
        with WorktreeRepository.open(cwd) as repository:
            worktrees = repository.linked()
            # Not gated behind the details flag: it is two stat calls on a
            # repository that is already open, where the details cost a merge
            # base per branch and a second repository object.
            repository_cwd = repository.repository_counterpart
    except GitRepositoryNotFoundError:
        logger.debug("Skipping worktree listing for non-git path=%s", cwd)
        worktrees = ()
    except GitUnavailableError:
        # app-server is expected to run without git; a host that cannot list
        # worktrees simply has none, so callers should not see invalid_params.
        logger.debug("Skipping worktree listing without git cwd=%s", cwd)
        worktrees = ()

    details = (
        _worktree_details(cwd, worktrees) if include_details and worktrees else None
    )
    changes = details.changes if details else {}
    return WorkspaceWorktreeListResponse(
        repository_branch=details.repository_branch if details else None,
        repository_cwd=str(repository_cwd) if repository_cwd is not None else None,
        worktrees=[
            WorkspaceLinkedWorktree(
                name=worktree.name,
                branch=worktree.branch,
                cwd=str(worktree.path),
                root=str(worktree.root),
                repo_root=str(worktree.repo_root),
                branch_changes=changes.get(worktree.branch),
            )
            for worktree in worktrees
        ],
    )


@dataclass(frozen=True)
class _WorktreeDetails:
    repository_branch: str | None
    changes: dict[str, WorkspaceGitBranchChanges | None]


def _worktree_details(
    cwd: Path, worktrees: Sequence[LinkedWorktree]
) -> _WorktreeDetails:
    """What the listing reports only when a caller renders it.

    The counts come from one checkout rather than one per worktree: every
    worktree is a ref in the same object database, and each repository object
    holds its cat-file children until it is closed.

    The main checkout is opened separately, because it is the one working tree
    this listing never names and asking *it* is the only way to learn which
    branch it is on.
    """
    # `GitRepo.open` raises where the old soft opener answered None; a path that
    # is not a checkout has no details to report either way.
    try:
        checkout = GitRepo.open(cwd)
    except GitError:
        return _WorktreeDetails(repository_branch=None, changes={})
    with checkout:
        changes = {
            worktree.branch: (
                WorkspaceGitBranchChanges(
                    additions=counted.additions, deletions=counted.deletions
                )
                if (counted := checkout.changes_on(worktree.branch)) is not None
                else None
            )
            for worktree in worktrees
        }
    try:
        repository = GitRepo.open(worktrees[0].repo_root)
    except GitError:
        return _WorktreeDetails(repository_branch=None, changes=changes)
    with repository:
        return _WorktreeDetails(repository_branch=repository.branch(), changes=changes)


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


def _contains(parent: Path, descendant: Path) -> bool:
    """Whether *descendant* is *parent* or sits underneath it."""
    return parent == descendant or parent in descendant.parents


# The deepest wins, so a worktree nested inside another is not lost to its
# container.
def _deepest_containing(
    checkouts: Sequence[tuple[Path, str | None]], probe: Path
) -> tuple[Path, str | None] | None:
    matches = [entry for entry in checkouts if _contains(entry[0], probe)]
    return max(matches, key=lambda entry: len(str(entry[0])), default=None)


def _repository_holding(
    roots_by_repo: dict[str, tuple[Path, ...]], session_cwd: Path
) -> str | None:
    """Which linked repository the session is working in, if any of them is.

    A managed worktree lives outside the repository it belongs to, so a
    repository holds the session when its own directory contains the cwd *or*
    one of its worktrees does. The deepest match wins, so a repository linked
    inside another takes the session from its ancestor rather than both
    claiming it. That is what makes this one decision across every repository
    rather than a question each can answer alone.

    Containment is a question about paths, so a repository competes here even
    when git could not be read from it and its worktrees are unknown. Skipping
    it would hand the session to a shallower ancestor and report that
    ancestor's branch for a session that is not in it.
    """
    holder: str | None = None
    depth = -1
    for repo_local_path, roots in roots_by_repo.items():
        for root in (Path(repo_local_path).resolve(), *roots):
            if not _contains(root, session_cwd) or len(str(root)) <= depth:
                continue
            holder = repo_local_path
            depth = len(str(root))
    return holder


def _checkout_for_repo(
    repo_local_path: str,
    repository: WorktreeRepository,
    linked: Sequence[LinkedWorktree],
    checkouts: Sequence[tuple[Path, str | None]],
    session_cwd: Path | None,
) -> WorkspaceGitCheckout:
    probe = (session_cwd or Path(repo_local_path)).resolve()
    status = repository.status()
    # Where the session actually is, across every checkout git reports. The
    # listing below keeps only the managed ones, so asking it alone would name
    # the repository's own branch for a session sitting in a worktree that is
    # detached, prunable, or fails validation.
    holding = _deepest_containing(checkouts, probe)
    # The listing reports linked worktrees only, so a session in the
    # repository's own checkout matches nothing here. It is still on a branch,
    # which is why `worktree` and `branch` are separate fields.
    match = next(
        (worktree for worktree in linked if _contains(worktree.root, probe)), None
    )
    # Per-checkout facts come from the checkout the session is in; the rest are
    # the repository's and read the same from any of them.
    return WorkspaceGitCheckout(
        repo_local_path=repo_local_path,
        ok=True,
        is_primary=session_cwd is not None,
        repo_url=status.repo_url,
        root=str(holding[0] if holding is not None else status.root),
        worktree=match.name if match is not None else None,
        branch=holding[1] if holding is not None else status.branch,
        base_branch=status.base_branch,
    )


def _unreadable_checkout(
    repo_local_path: str, reason: str, *, is_primary: bool
) -> WorkspaceGitCheckout:
    # No path in the message: packaged Desktop forwards warn logs to Sentry as
    # breadcrumbs and its scrubber does not redact paths. The response carries
    # the reason, which reaches only this session's own renderer.
    logger.debug("Failed to read the git checkout of a linked repository")
    return WorkspaceGitCheckout(
        repo_local_path=repo_local_path, ok=False, is_primary=is_primary, message=reason
    )


def git_checkouts_response(
    repo_local_paths: Sequence[str], session_cwd: Path | None
) -> WorkspaceGitCheckoutsResponse:
    """Every repository a project links, read as git.

    Worktrees for all of them first, because whether a repository holds the
    session cannot be decided from any one of them alone. Then each is read for
    status, and only the holder is probed at the session's directory rather
    than at its own root.

    The repositories stay open across both passes, which is the point: the
    listing and the status answer about the same checkout, and each open leaves
    ``git cat-file --batch`` children holding handles into .git until it is
    closed. Opening once per repository rather than once per question is what
    this route exists to do.
    """
    with ExitStack() as stack:
        opened: dict[str, WorktreeRepository] = {}
        linked_by_repo: dict[str, tuple[LinkedWorktree, ...]] = {}
        checkouts_by_repo: dict[str, tuple[tuple[Path, str | None], ...]] = {}
        unreadable: dict[str, str] = {}
        absent: set[str] = set()
        for repo_local_path in repo_local_paths:
            try:
                repository = stack.enter_context(
                    WorktreeRepository.open(Path(repo_local_path))
                )
                linked_by_repo[repo_local_path] = repository.linked()
                checkouts_by_repo[repo_local_path] = repository.checkouts()
                opened[repo_local_path] = repository
            except GitRepositoryNotFoundError:
                # Not a checkout at all, which is not a failure to report: a
                # project may link a directory git knows nothing about, and
                # saying so per repository would put a permanent error in the
                # header. Left out of the answer entirely.
                absent.add(repo_local_path)
            except GitError as exc:
                unreadable[repo_local_path] = str(exc)

        home = (
            _repository_holding(
                {
                    # Every checkout, not the managed subset: a session sitting
                    # in a detached or prunable worktree is still in this
                    # repository, and reading only `linked()` would leave the
                    # repository looking as though it held no session at all.
                    repo_local_path: tuple(
                        root for root, _ in checkouts_by_repo.get(repo_local_path, ())
                    )
                    for repo_local_path in repo_local_paths
                    # A path git knows nothing about is left out of the answer
                    # entirely, so letting it win here would mark nothing
                    # primary and take the header from the repository that
                    # actually holds the session. Unreadable ones stay: they
                    # get an entry, and it can carry the mark.
                    if repo_local_path not in absent
                },
                session_cwd.resolve(),
            )
            if session_cwd is not None
            else None
        )

        checkouts: list[WorkspaceGitCheckout] = []
        for repo_local_path in repo_local_paths:
            if repo_local_path in absent:
                continue
            is_primary = repo_local_path == home
            if (reason := unreadable.get(repo_local_path)) is not None:
                checkouts.append(
                    _unreadable_checkout(repo_local_path, reason, is_primary=is_primary)
                )
                continue
            try:
                checkouts.append(
                    _checkout_for_repo(
                        repo_local_path,
                        opened[repo_local_path],
                        linked_by_repo[repo_local_path],
                        checkouts_by_repo[repo_local_path],
                        session_cwd if is_primary else None,
                    )
                )
            except GitRepositoryNotFoundError:
                continue
            except GitError as exc:
                checkouts.append(
                    _unreadable_checkout(
                        repo_local_path, str(exc), is_primary=is_primary
                    )
                )
        return WorkspaceGitCheckoutsResponse(checkouts=checkouts)


def _is_counterpart(cwd: Path, target: Path) -> bool:
    """Whether *target* is where a session at *cwd* sits, in another checkout.

    The detached mirror of ``AgentLoop._is_derived_destination``: the same
    narrowing, so a session cannot reach a tree it never occupied whether or
    not an agent happens to be holding it. Git refusing to answer is a no,
    since a destination that cannot be shown to be a counterpart is not one.
    """
    try:
        with WorktreeRepository.open(cwd) as repository:
            counterparts = {worktree.path.resolve() for worktree in repository.linked()}
            if (in_repository := repository.repository_counterpart) is not None:
                counterparts.add(in_repository)
    except GitError:
        return False
    return target in counterparts
