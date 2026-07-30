from __future__ import annotations

import asyncio
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
from vibe.app_server._projection import project_message_history
from vibe.app_server._state import build_stored_public_state, history_page
from vibe.app_server._workspace import (
    WorkspaceTrustError,
    decide_workspace_trust,
    read_workspace_trust,
)
from vibe.app_server.protocol import (
    ConfigSchemaReadParams,
    ConfigSchemaReadResponse,
    EmptyResponse,
    HistoryListParams,
    HistoryListResponse,
    ProjectLinkMutationResponse,
    ProjectLinksCreateParams,
    ProjectLinksLinkParams,
    ProjectLinksListParams,
    ProjectLinksListResponse,
    ProjectLinksPickerLoadMoreParams,
    ProjectLinksPickerLoadMoreResponse,
    ProjectLinksPickerLoadParams,
    ProjectLinksPickerLoadResponse,
    ProjectLinksResolveRootParams,
    ProjectLinksResolveRootResponse,
    ProjectLinksUnlinkParams,
    ProjectLinksUnlinkResponse,
    ProtocolErrorCode,
    SessionDeleteParams,
    SessionListParams,
    SessionListResponse,
    SessionReadParams,
    SessionReadResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceTrustDecisionParams,
    WorkspaceTrustStatusParams,
)
from vibe.core.config import VibeConfigSchema, build_default_orchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.session.resume_sessions import list_local_resume_sessions
from vibe.core.session.saved_sessions import (
    delete_saved_session,
    update_saved_session_title,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import LLMMessage, SessionMetadata

_HOST_METHODS = frozenset({
    "config/schema",
    "history/list",
    "projectLinks/create",
    "projectLinks/link",
    "projectLinks/list",
    "projectLinks/picker/load",
    "projectLinks/picker/loadMore",
    "projectLinks/resolveRoot",
    "projectLinks/unlink",
    "session/delete",
    "session/list",
    "session/read",
    "session/title/update",
    "workspace/trust/decision",
    "workspace/trust/status",
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
            case "session/list":
                params = validate_wire(SessionListParams, raw_params)
                config = await self._load_config(params.cwd)
                response = await asyncio.to_thread(
                    project_session_list, config, params.cwd
                )
            case "session/read":
                params = validate_wire(SessionReadParams, raw_params)
                config = await self._load_config(None)
                response = await asyncio.to_thread(self._read_session, params, config)
            case "session/delete":
                params = validate_wire(SessionDeleteParams, raw_params)
                config = await self._load_config(None)
                await delete_saved_session(params.session_id, config.session_logging)
                response = EmptyResponse()
            case "session/title/update":
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
                updated_at = metadata.get("end_time")
                title = metadata.get("title")
                if not isinstance(title, str):
                    raise RuntimeError("The saved session title was not updated")
                response = SessionTitleUpdateResponse(
                    title=title,
                    updated_at=updated_at if isinstance(updated_at, str) else None,
                )
            case "history/list":
                params = validate_wire(HistoryListParams, raw_params)
                config = await self._load_config(None)
                response = await asyncio.to_thread(self._list_history, params, config)
            case "workspace/trust/status":
                params = validate_wire(WorkspaceTrustStatusParams, raw_params)
                response = await asyncio.to_thread(
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
            case _ if method.startswith("projectLinks/"):
                response = await self._dispatch_project_links(method, raw_params)
            case _:
                raise method_not_found(method)
        return response

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
            case "projectLinks/unlink":
                params = validate_wire(ProjectLinksUnlinkParams, raw_params)
                response = ProjectLinksUnlinkResponse.model_validate(
                    await self._project_links.unlink(params.root_path)
                )
            case _:
                raise method_not_found(method)
        return response

    def _read_session(
        self, params: SessionReadParams, config: VibeConfigSchema
    ) -> SessionReadResponse:
        messages, metadata = self._load_session(params.session_id, config)
        return SessionReadResponse(
            state=build_stored_public_state(
                params.session_id,
                messages,
                metadata,
                history_limit=params.history_limit,
            )
        )

    def _list_history(
        self, params: HistoryListParams, config: VibeConfigSchema
    ) -> HistoryListResponse:
        messages, metadata = self._load_session(params.session_id, config)
        all_history = project_message_history(params.session_id, messages, metadata)
        return HistoryListResponse(
            history=history_page(
                all_history,
                turn_id=params.turn_id,
                before=params.before,
                after=params.after,
                limit=params.limit,
            )
        )

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
        session_files = self._harness_files.for_session(self._cwd(cwd))
        orchestrator = await build_default_orchestrator(
            harness_files=session_files, require_api_key=False
        )
        return orchestrator.config

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
    config: VibeConfigSchema, cwd: str | None
) -> SessionListResponse:
    sessions = list_local_resume_sessions(config, cwd)
    return SessionListResponse.model_validate({
        "sessions": [
            {
                "session_id": session.session_id,
                "cwd": session.cwd,
                "parent_session_id": session.parent_session_id,
                "title": session.title,
                "end_time": session.end_time,
                "preview": SessionLoader.get_first_user_message(
                    session.session_id, config.session_logging
                ),
            }
            for session in sessions
        ]
    })
