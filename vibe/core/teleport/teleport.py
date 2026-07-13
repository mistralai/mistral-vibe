from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from pathlib import Path
import types
from uuid import uuid4

import httpx
import zstandard

from vibe.core.config import AnyVibeConfig
from vibe.core.session.session_logger import SessionLogger
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.git import GitRepoInfo, GitRepository
from vibe.core.teleport.nuage import (
    NuageClient,
    NuageContext,
    NuageDiff,
    NuageMessage,
    NuageRepository,
    NuageRequest,
    NuageTextPart,
)
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportMessageContext,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportSendEvent,
    TeleportStartingWorkflowEvent,
    TeleportYieldEvent,
)
from vibe.core.utils.http import VibeAsyncHTTPClient, build_ssl_context
from vibe.core.vibe_code_project import VibeProjectsStore, is_saved_project_stale_error


class TeleportService:
    def __init__(
        self,
        session_logger: SessionLogger,
        vibe_code_sessions_base_url: str,
        vibe_code_api_key: str,
        workdir: Path | None = None,
        *,
        vibe_config: AnyVibeConfig | None = None,
        client: VibeAsyncHTTPClient | None = None,
        project_store: VibeProjectsStore | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._session_logger = session_logger
        self._vibe_code_sessions_base_url = vibe_code_sessions_base_url
        self._vibe_code_api_key = vibe_code_api_key
        self._vibe_config = vibe_config
        self._git = GitRepository(workdir)
        self._client = client
        self._project_store = project_store or VibeProjectsStore()
        self._owns_client = client is None
        self._timeout = timeout
        self._nuage_client_instance: NuageClient | None = None

    async def __aenter__(self) -> TeleportService:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(self._timeout), verify=build_ssl_context()
            )
        self._nuage_client_instance = NuageClient(
            self._vibe_code_sessions_base_url,
            self._vibe_code_api_key,
            client=self._client,
        )
        await self._git.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        await self._git.__aexit__(exc_type, exc_val, exc_tb)
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http_client(self) -> VibeAsyncHTTPClient:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(self._timeout), verify=build_ssl_context()
            )
            self._owns_client = True
        return self._client

    @property
    def _nuage_client(self) -> NuageClient:
        if self._nuage_client_instance is None:
            self._nuage_client_instance = NuageClient(
                self._vibe_code_sessions_base_url,
                self._vibe_code_api_key,
                client=self._http_client,
            )
        return self._nuage_client_instance

    async def check_supported(self) -> None:
        await self._git.get_info()

    async def is_supported(self) -> bool:
        return await self._git.is_supported()

    async def execute(
        self,
        prompt: str,
        *,
        project_id: str | None = None,
        message_context: TeleportMessageContext | None = None,
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportSendEvent]:
        if not prompt:
            raise ServiceTeleportError("Teleport requires a non-empty prompt.")
        self._validate_config()
        resolved_project_id = self._normalize_project_id(project_id=project_id)

        git_info = await self._git.get_info()
        if git_info.branch is None:
            raise ServiceTeleportError("Teleport requires a checked-out branch.")

        remote = git_info.remote_name
        yield TeleportCheckingGitEvent()
        await self._git.fetch(remote)
        commit_pushed, branch_pushed = await asyncio.gather(
            self._git.is_commit_pushed(git_info.commit, remote=remote, fetch=False),
            self._git.is_branch_pushed(remote=remote, fetch=False),
        )
        if not commit_pushed or not branch_pushed:
            unpushed_count = await self._git.get_unpushed_commit_count(remote)
            response = yield TeleportPushRequiredEvent(
                unpushed_count=max(1, unpushed_count),
                branch_not_pushed=not branch_pushed,
            )
            if (
                not isinstance(response, TeleportPushResponseEvent)
                or not response.approved
            ):
                raise ServiceTeleportError("Teleport cancelled: changes not pushed.")

            yield TeleportPushingEvent()
            await self._push_or_fail(remote)

        yield TeleportStartingWorkflowEvent()

        try:
            request = await asyncio.to_thread(
                self._build_nuage_request,
                prompt=prompt,
                git_info=git_info,
                project_id=resolved_project_id,
                message_context=message_context,
            )
            result = await self._nuage_client.start(request)
        except ServiceTeleportError as e:
            if resolved_project_id is not None and is_saved_project_stale_error(str(e)):
                if git_info.repo_root is not None:
                    await asyncio.to_thread(
                        self._project_store.delete_remote_project,
                        repo_root=git_info.repo_root,
                    )
            raise
        yield TeleportCompleteEvent(url=result.url)

    async def _push_or_fail(self, remote: str) -> None:
        if not await self._git.push_current_branch(remote):
            raise ServiceTeleportError(f"Failed to push current branch to {remote}.")

    def _validate_config(self) -> None:
        if not self._vibe_code_api_key:
            env_var = (
                self._vibe_config.vibe_code_api_key_env_var
                if self._vibe_config
                else "MISTRAL_API_KEY"
            )
            raise ServiceTeleportError(f"{env_var} not set.")

    def _build_nuage_request(
        self,
        *,
        prompt: str,
        git_info: GitRepoInfo,
        project_id: str,
        message_context: TeleportMessageContext | None = None,
    ) -> NuageRequest:
        compressed = self._compress_diff(git_info.diff)
        diff = (
            NuageDiff(content=compressed.decode("ascii"))
            if compressed is not None
            else None
        )

        message = NuageMessage(parts=[NuageTextPart(text=prompt)])
        context = NuageContext(
            repositories=[
                NuageRepository(
                    repo_url=git_info.remote_url,
                    branch=git_info.branch,
                    commit_sha=git_info.commit,
                    diff=diff,
                )
            ],
            message_context=message_context,
        )

        idempotency_key = str(uuid4())
        return NuageRequest(
            project_id=project_id,
            idempotency_key=idempotency_key,
            message=message,
            context=context,
        )

    def _normalize_project_id(self, *, project_id: str | None) -> str:
        if project_id is not None:
            normalized_project_id = project_id.strip()
            if normalized_project_id:
                return normalized_project_id

        raise ServiceTeleportError("Teleport requires a Vibe Code project id.")

    def _compress_diff(self, diff: str, max_size: int = 1_000_000) -> bytes | None:
        if not diff:
            return None
        compressed = zstandard.ZstdCompressor().compress(diff.encode("utf-8"))
        encoded = base64.b64encode(compressed)
        if len(encoded) > max_size:
            raise ServiceTeleportError(
                "Diff too large to teleport. Please commit and push your changes first."
            )
        return encoded
