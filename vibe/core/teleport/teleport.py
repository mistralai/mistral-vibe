from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator
from pathlib import Path
import types

import httpx
import zstandard

from vibe.core.config import VibeConfig
from vibe.core.session.session_logger import SessionLogger
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.git import GitRepoInfo, GitRepository
from vibe.core.teleport.nuage import (
    ChatAssistantParams,
    GitHubParams,
    NuageClient,
    TeleportSession,
    TextChunk,
    VibeAgent,
    WorkflowConfig,
    WorkflowIntegrations,
    WorkflowParams,
)
from vibe.core.teleport.types import (
    TeleportAuthCompleteEvent,
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportFetchingUrlEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportSendEvent,
    TeleportStartingWorkflowEvent,
    TeleportWaitingForGitHubEvent,
    TeleportYieldEvent,
)

_DEFAULT_TELEPORT_PROMPT = "Your session has been teleported on a remote workspace. Changes of workspace has been automatically teleported. External workspace changes has NOT been teleported. Environment variables has NOT been teleported. Please continue where you left off."


class TeleportService:
    def __init__(
        self,
        session_logger: SessionLogger,
        vibe_code_base_url: str,
        vibe_code_workflow_id: str,
        vibe_code_api_key: str,
        workdir: Path | None = None,
        *,
        vibe_code_task_queue: str | None = None,
        vibe_config: VibeConfig | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._session_logger = session_logger
        self._vibe_code_base_url = vibe_code_base_url
        self._vibe_code_workflow_id = vibe_code_workflow_id
        self._vibe_code_api_key = vibe_code_api_key
        self._vibe_code_task_queue = vibe_code_task_queue
        self._vibe_code_project_name = (
            vibe_config.vibe_code_project_name if vibe_config else None
        )
        self._vibe_config = vibe_config
        self._git = GitRepository(workdir)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._nuage_client_instance: NuageClient | None = None

    async def __aenter__(self) -> TeleportService:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        self._nuage_client_instance = NuageClient(
            self._vibe_code_base_url,
            self._vibe_code_api_key,
            self._vibe_code_workflow_id,
            task_queue=self._vibe_code_task_queue,
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
    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
            self._owns_client = True
        return self._client

    @property
    def _nuage_client(self) -> NuageClient:
        if self._nuage_client_instance is None:
            self._nuage_client_instance = NuageClient(
                self._vibe_code_base_url,
                self._vibe_code_api_key,
                self._vibe_code_workflow_id,
                task_queue=self._vibe_code_task_queue,
                client=self._http_client,
            )
        return self._nuage_client_instance

    async def check_supported(self) -> None:
        await self._git.get_info()

    async def is_supported(self) -> bool:
        return await self._git.is_supported()

    async def execute(
        self, prompt: str | None, session: TeleportSession
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportSendEvent]:
        if prompt:
            lechat_user_message = prompt
        else:
            last_user_message = self._get_last_user_message(session)
            if not last_user_message:
                raise ServiceTeleportError(
                    "No prompt provided and no user message found in session."
                )
            lechat_user_message = f"{last_user_message} (continue)"
            prompt = _DEFAULT_TELEPORT_PROMPT
        self._validate_config()

        git_info = await self._git.get_info()

        yield TeleportCheckingGitEvent()
        await self._git.fetch()
        commit_pushed, branch_pushed = await asyncio.gather(
            self._git.is_commit_pushed(git_info.commit, fetch=False),
            self._git.is_branch_pushed(fetch=False),
        )
        if not commit_pushed or not branch_pushed:
            unpushed_count = await self._git.get_unpushed_commit_count()
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
            await self._push_or_fail()

        yield TeleportStartingWorkflowEvent()

        execution_id = await self._nuage_client.start_workflow(
            WorkflowParams(
                prompt=prompt,
                message=[TextChunk(text=lechat_user_message)],
                config=WorkflowConfig(
                    agent=VibeAgent(
                        vibe_config=self._vibe_config.model_dump()
                        if self._vibe_config
                        else None,
                        session=session,
                    )
                ),
                integrations=WorkflowIntegrations(
                    github=self._build_github_params(git_info),
                    chat_assistant=ChatAssistantParams(
                        create_thread=True,
                        user_message=lechat_user_message,
                        project_name=self._vibe_code_project_name,
                    ),
                ),
            )
        )

        yield TeleportWaitingForGitHubEvent()

        auth_event_sent = False
        async for github_data in self._nuage_client.wait_for_github_connection(
            execution_id
        ):
            if github_data.connected:
                break
            if not auth_event_sent and github_data.oauth_url:
                yield TeleportAuthRequiredEvent(
                    oauth_url=github_data.oauth_url, message=github_data.error
                )
                auth_event_sent = True
            if github_data.error:
                yield TeleportWaitingForGitHubEvent(message=github_data.error)

        yield TeleportAuthCompleteEvent()

        yield TeleportFetchingUrlEvent()
        chat_url = await self._nuage_client.get_chat_assistant_url(execution_id)

        if not chat_url:
            raise ServiceTeleportError("Chat assistant URL is not available yet")

        yield TeleportCompleteEvent(url=chat_url)

    async def _push_or_fail(self) -> None:
        if not await self._git.push_current_branch():
            raise ServiceTeleportError("Failed to push current branch to remote.")

    def _validate_config(self) -> None:
        if not self._vibe_code_api_key:
            env_var = (
                self._vibe_config.vibe_code_api_key_env_var
                if self._vibe_config
                else "MISTRAL_API_KEY"
            )
            raise ServiceTeleportError(f"{env_var} not set.")

    def _build_github_params(self, git_info: GitRepoInfo) -> GitHubParams:
        return GitHubParams(
            repo=f"{git_info.owner}/{git_info.repo}",
            branch=git_info.branch,
            commit=git_info.commit,
            teleported_diffs=self._compress_diff(git_info.diff or ""),
        )

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

    def _get_last_user_message(self, session: TeleportSession) -> str | None:
        for msg in reversed(session.messages):
            if msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str) and content:
                    return content
        return None
