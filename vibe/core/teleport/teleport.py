from __future__ import annotations

import base64
from collections.abc import AsyncGenerator
from pathlib import Path
import types

import httpx
import zstandard

from vibe.core.auth.github import GitHubAuthProvider
from vibe.core.session.session_logger import SessionLogger
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.git import GitRepoInfo, GitRepository
from vibe.core.teleport.nuage import (
    GitRepoConfig,
    NuageClient,
    TeleportSession,
    VibeNewSandbox,
    VibeSandboxConfig,
    WorkflowParams,
)
from vibe.core.teleport.types import (
    TeleportAuthCompleteEvent,
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportSendEvent,
    TeleportSendingGithubTokenEvent,
    TeleportStartingWorkflowEvent,
    TeleportYieldEvent,
)

# TODO(vibe-nuage): update URL once prod has shared vibe-nuage workers
_NUAGE_EXECUTION_URL_TEMPLATE = "https://console.globalaegis.net/build/workflows/{workflow_id}?tab=executions&executionId={execution_id}"
_DEFAULT_TELEPORT_PROMPT = "please continue where you left off"


class TeleportService:
    def __init__(
        self,
        session_logger: SessionLogger,
        nuage_base_url: str,
        nuage_workflow_id: str,
        nuage_api_key: str,
        workdir: Path | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._session_logger = session_logger
        self._nuage_base_url = nuage_base_url
        self._nuage_workflow_id = nuage_workflow_id
        self._nuage_api_key = nuage_api_key
        self._git = GitRepository(workdir)
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout
        self._github_auth: GitHubAuthProvider | None = None
        self._nuage: NuageClient | None = None

    async def __aenter__(self) -> TeleportService:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        self._github_auth = GitHubAuthProvider(client=self._client)
        self._nuage = NuageClient(
            self._nuage_base_url,
            self._nuage_api_key,
            self._nuage_workflow_id,
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
    def _github_auth_provider(self) -> GitHubAuthProvider:
        if self._github_auth is None:
            self._github_auth = GitHubAuthProvider(client=self._http_client)
        return self._github_auth

    @property
    def _nuage_client(self) -> NuageClient:
        if self._nuage is None:
            self._nuage = NuageClient(
                self._nuage_base_url,
                self._nuage_api_key,
                self._nuage_workflow_id,
                client=self._http_client,
            )
        return self._nuage

    async def check_supported(self) -> None:
        await self._git.get_info()

    async def is_supported(self) -> bool:
        return await self._git.is_supported()

    async def execute(
        self, prompt: str | None, session: TeleportSession
    ) -> AsyncGenerator[TeleportYieldEvent, TeleportSendEvent]:
        prompt = prompt or _DEFAULT_TELEPORT_PROMPT
        self._validate_config()

        git_info = await self._git.get_info()

        yield TeleportCheckingGitEvent()
        if not await self._git.is_commit_pushed(git_info.commit):
            unpushed_count = await self._git.get_unpushed_commit_count()
            response = yield TeleportPushRequiredEvent(
                unpushed_count=max(1, unpushed_count)
            )
            if (
                not isinstance(response, TeleportPushResponseEvent)
                or not response.approved
            ):
                raise ServiceTeleportError("Teleport cancelled: commit not pushed.")

            yield TeleportPushingEvent()
            await self._push_or_fail()

        github_token = await self._github_auth_provider.get_valid_token()

        if not github_token:
            handle = await self._github_auth_provider.start_device_flow(
                open_browser=True
            )
            yield TeleportAuthRequiredEvent(
                user_code=handle.info.user_code,
                verification_uri=handle.info.verification_uri,
            )
            github_token = await self._github_auth_provider.wait_for_token(handle)
            yield TeleportAuthCompleteEvent()

        yield TeleportStartingWorkflowEvent()

        execution_id = await self._nuage_client.start_workflow(
            WorkflowParams(
                prompt=prompt, sandbox=self._build_sandbox(git_info), session=session
            )
        )

        yield TeleportSendingGithubTokenEvent()
        await self._nuage_client.send_github_token(execution_id, github_token)

        chat_url = _NUAGE_EXECUTION_URL_TEMPLATE.format(
            workflow_id=self._nuage_workflow_id, execution_id=execution_id
        )
        # chat_url = await nuage.create_le_chat_thread(
        #     execution_id=execution_id, user_message=prompt
        # )

        yield TeleportCompleteEvent(url=chat_url)

    async def _push_or_fail(self) -> None:
        if not await self._git.push_current_branch():
            raise ServiceTeleportError("Failed to push current branch to remote.")

    def _validate_config(self) -> None:
        # TODO(vibe-nuage): update error message once prod has shared vibe-nuage workers
        if not self._nuage_api_key:
            raise ServiceTeleportError(
                "STAGING_MISTRAL_API_KEY not set. "
                "Set it from https://console.globalaegis.net/ to use teleport."
            )

    def _build_sandbox(self, git_info: GitRepoInfo) -> VibeNewSandbox:
        return VibeNewSandbox(
            config=VibeSandboxConfig(
                git_repo=GitRepoConfig(
                    url=git_info.remote_url,
                    branch=git_info.branch,
                    commit=git_info.commit,
                )
            ),
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
