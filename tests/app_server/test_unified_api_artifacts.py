"""PR/MR artifacts the adapter detects in shell output arriving on the Session API stream."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from mistralai_vibe_local_harness.protocol import (
    RustCompletionResult,
    RustCompletionResultPart,
    RustCompletionResultToolCallPart,
    RustCompletionSucceededEvent,
    RustTextContentBlock,
    RustTokenUsage,
)
from mistralai_vibe_local_harness.vibe import _local_actions
from tests.app_server._session_api_harness import api_adapter
from tests.stubs.app_server import FakeSessionBackendServices
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.events import TurnCompleted
from vibe.app_server.models import TextContentBlock
from vibe.app_server.protocol import (
    ArtifactCreatedParams,
    SessionOptions,
    TurnStartParams,
)
from vibe.core.git.forge_sync import ForgeSyncError

PR_URL = "https://github.com/mistralai/vibe/pull/123"
# A shell function stands in for the gh CLI, so the command runs for real and
# prints the URL without reaching GitHub.
COMMAND = f"gh() {{ echo {PR_URL}; }}; gh pr create --title 'Fix login'"


@pytest.fixture
def model_creates_a_pr(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model that runs ``COMMAND`` with the bash tool, then answers."""
    calls = 0

    async def execute_completion(
        action: Any, _messages: Any, _tools: Any, _config: Any, **_kwargs: Any
    ) -> RustCompletionSucceededEvent:
        nonlocal calls
        calls += 1
        parts: list[RustCompletionResultPart] = (
            [
                RustCompletionResultToolCallPart(
                    id="0123456789abcdef",
                    name="bash",
                    arguments_json=json.dumps({"command": COMMAND}),
                )
            ]
            if calls == 1
            else [RustTextContentBlock(text="done")]
        )
        return RustCompletionSucceededEvent(
            action_id=action.action_id,
            result=RustCompletionResult(
                parts=parts,
                finish_reason="tool_call" if calls == 1 else "stop",
                usage=RustTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            ),
        )

    monkeypatch.setattr(_local_actions, "execute_completion", execute_completion)


@pytest.fixture(autouse=True)
def _no_provider_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail every provider read, so the artifact keeps its detected data."""
    from vibe.app_server import _pull_request_cache as cache_module

    def fail(*_args: object, **_kwargs: object) -> None:
        raise ForgeSyncError("no provider fetch in tests")

    monkeypatch.setattr(cache_module, "find_cli_binary", lambda name: name)
    monkeypatch.setattr(cache_module, "read_github_checks", fail)
    monkeypatch.setattr(cache_module, "read_github_pull_requests", fail)


@pytest.mark.asyncio
async def test_a_pr_created_by_a_shell_command_is_announced_to_the_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_creates_a_pr: None,
    process_services: ProcessServices,
) -> None:
    """*Prepare*: PR monitoring on, a real adapter on the Session API path with a
        recording client, and a model whose bash call prints a PR URL after
        ``gh pr create``.
    *Do*: Run the Turn and read the stream until it completes.
    *Assert*: The client receives one ``artifacts/created`` for that PR.
    """
    # Prepare
    monkeypatch.setenv("VIBE_EXPERIMENTAL_ENABLE_PR_MONITORING", "true")
    services = FakeSessionBackendServices()
    options = SessionOptions(cwd=str(tmp_path), auto_approve=True)
    async with api_adapter(
        tmp_path, options=options, process_services=process_services
    ) as harness:
        adapter = harness.adapter
        adapter._services = services

        # Do
        await adapter.start_turn(
            TurnStartParams(
                session_id=adapter.session_id,
                message=[TextContentBlock(text="Open the PR")],
            )
        )
        async with asyncio.timeout(10):
            async for backend_event in harness.events:
                if isinstance(backend_event.event, TurnCompleted):
                    break

    # Assert
    created = [
        params
        for method, params in services.notifications
        if method == "artifacts/created"
    ]
    assert len(created) == 1
    assert isinstance(created[0], ArtifactCreatedParams)
    assert created[0].session_id == adapter.session_id
    assert created[0].artifact.external_url == PR_URL
