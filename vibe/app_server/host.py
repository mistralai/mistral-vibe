from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from vibe.app_server._model import validate_wire
from vibe.app_server.client import AppServerClient
from vibe.app_server.client_tools import ClientToolHandler
from vibe.app_server.models import (
    PublicSession,
    PublicSessionState,
    WorkspaceTrustDecision,
)
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigSchemaReadParams,
    ConfigSchemaReadResponse,
    EmptyResponse,
    PageRequest,
    SessionDeleteParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionKind,
    SessionListParams,
    SessionListResponse,
    SessionOptions,
    SessionReadParams,
    SessionReadResponse,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    WorkspaceTrustDecisionParams,
    WorkspaceTrustStatusParams,
    WorkspaceTrustStatusResponse,
)

if TYPE_CHECKING:
    from vibe.app_server.session import AppServerSession


class AppServerHost:
    def __init__(
        self,
        client: AppServerClient,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        session_options: SessionOptions,
        *,
        resume_session_id: str | None = None,
        continue_session: bool = False,
        client_tool_handler: ClientToolHandler | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> None:
        if resume_session_id is not None and continue_session:
            raise ValueError("Cannot resume a specific session and continue the latest")
        self._client = client
        self._client_info = client_info
        self._capabilities = capabilities
        self._session_options = session_options
        self._resume_session_id = resume_session_id
        self._continue_session = continue_session
        self._client_tool_handler = client_tool_handler
        self._client_factory = client_factory
        self._transferred = False

    @classmethod
    async def connect(
        cls,
        client: AppServerClient,
        *,
        client_info: ClientInfo,
        capabilities: ClientCapabilities,
        session_options: SessionOptions | None = None,
        resume_session_id: str | None = None,
        continue_session: bool = False,
        client_tool_handler: ClientToolHandler | None = None,
        client_factory: Callable[[], AppServerClient] | None = None,
    ) -> AppServerHost:
        try:
            await client.start()
            await client.initialize(client_info, capabilities)
            await client.notify("initialized")
        except BaseException:
            await client.close()
            raise
        return cls(
            client,
            client_info,
            capabilities,
            session_options or SessionOptions(),
            resume_session_id=resume_session_id,
            continue_session=continue_session,
            client_tool_handler=client_tool_handler,
            client_factory=client_factory,
        )

    @property
    def cwd(self) -> str | None:
        return self._session_options.cwd

    async def open_session(self) -> AppServerSession:
        return await self._open_session(self._resume_session_id, self._continue_session)

    async def start_session(self) -> AppServerSession:
        # Backs the --resume picker; the throwaway session must not emit
        # new-session telemetry since it is discarded on resume.
        return await self._open_session(None, False, session_kind=SessionKind.EPHEMERAL)

    async def resume_session(self, session_id: str) -> AppServerSession:
        return await self._open_session(session_id, False)

    async def continue_session(self) -> AppServerSession:
        return await self._open_session(None, True)

    async def _open_session(
        self,
        resume_session_id: str | None,
        continue_session: bool,
        *,
        session_kind: SessionKind = SessionKind.NORMAL,
    ) -> AppServerSession:
        from vibe.app_server.session import AppServerSession

        if self._transferred:
            raise RuntimeError("The app-server host connection already owns a session")
        session = await AppServerSession.open(
            self._client,
            client_info=self._client_info,
            capabilities=self._capabilities,
            session_options=self._session_options,
            resume_session_id=resume_session_id,
            continue_session=continue_session,
            session_kind=session_kind,
            client_tool_handler=self._client_tool_handler,
            client_factory=self._client_factory,
        )
        self._transferred = True
        return session

    async def list_sessions(self, cwd: str | None = None) -> list[PublicSession]:
        self._require_host()
        cursor: str | None = None
        sessions: list[PublicSession] = []
        while True:
            response = validate_wire(
                SessionListResponse,
                await self._client.request(
                    "session/list", SessionListParams(cwd=cwd, cursor=cursor)
                ),
            )
            sessions.extend(response.items)
            if response.next_cursor is None:
                break
            cursor = response.next_cursor
        return sessions

    async def read_session(
        self, session_id: str, *, history_limit: int = 200
    ) -> PublicSessionState:
        self._require_host()
        return validate_wire(
            SessionReadResponse,
            await self._client.request(
                "session/read",
                SessionReadParams(
                    session_id=session_id, history=PageRequest(limit=history_limit)
                ),
            ),
        ).state

    async def list_history(
        self,
        session_id: str,
        *,
        before: str | None = None,
        after: str | None = None,
        limit: int = 200,
    ) -> SessionHistoryListResponse:
        self._require_host()
        response = validate_wire(
            SessionHistoryListResponse,
            await self._client.request(
                "session/history/list",
                SessionHistoryListParams(
                    session_id=session_id,
                    page=PageRequest(
                        cursor=before or after,
                        limit=limit,
                        direction=(
                            "forward"
                            if after is not None and before is None
                            else "backward"
                        ),
                    ),
                ),
            ),
        )
        return response

    async def delete_session(self, session_id: str) -> None:
        self._require_host()
        validate_wire(
            EmptyResponse,
            await self._client.request(
                "session/delete", SessionDeleteParams(session_id=session_id)
            ),
        )

    async def rename_session(
        self, session_id: str, title: str
    ) -> SessionTitleUpdateResponse:
        self._require_host()
        return validate_wire(
            SessionTitleUpdateResponse,
            await self._client.request(
                "session/rename",
                SessionTitleUpdateParams(session_id=session_id, title=title),
            ),
        )

    async def read_config_schema(self) -> ConfigSchemaReadResponse:
        self._require_host()
        return validate_wire(
            ConfigSchemaReadResponse,
            await self._client.request("config/schema", ConfigSchemaReadParams()),
        )

    async def read_config(self) -> ConfigReadResponse:
        self._require_host()
        return validate_wire(
            ConfigReadResponse,
            await self._client.request("config/read", ConfigReadParams()),
        )

    async def trust_status(
        self, cwd: str | None = None
    ) -> WorkspaceTrustStatusResponse:
        self._require_host()
        return validate_wire(
            WorkspaceTrustStatusResponse,
            await self._client.request(
                "workspace/trust/status", WorkspaceTrustStatusParams(cwd=cwd)
            ),
        )

    async def decide_trust(
        self, decision: WorkspaceTrustDecision, *, cwd: str | None = None
    ) -> WorkspaceTrustStatusResponse:
        self._require_host()
        return validate_wire(
            WorkspaceTrustStatusResponse,
            await self._client.request(
                "workspace/trust/decision",
                WorkspaceTrustDecisionParams(decision=decision, cwd=cwd),
            ),
        )

    async def close(self) -> None:
        if self._transferred:
            return
        await self._client.close()

    def _require_host(self) -> None:
        if self._transferred:
            raise RuntimeError("The app-server connection belongs to an open session")
