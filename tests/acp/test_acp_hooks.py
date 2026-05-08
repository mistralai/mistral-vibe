from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tomli_w

from tests.conftest import get_base_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.hooks.models import HookConfigResult, HookType
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role, SessionMetadata


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend(
        LLMChunk(
            message=LLMMessage(role=Role.assistant, content="Hi"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )
    )


def _write_config(config_dir: Path, *, enable_hooks: bool) -> None:
    config = get_base_config()
    config["enable_experimental_hooks"] = enable_hooks
    with (config_dir / "config.toml").open("wb") as f:
        tomli_w.dump(config, f)


def _create_acp_agent() -> VibeAcpAgentLoop:
    agent = VibeAcpAgentLoop()
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent


def _spy_agent_loop(backend: FakeBackend) -> tuple[MagicMock, type[AgentLoop]]:
    spy = MagicMock()

    class Patched(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            spy(*args, **kwargs)
            super().__init__(*args, **kwargs, backend=backend)

    return spy, Patched


def _hook_config_from_spy(spy: MagicMock) -> HookConfigResult | None:
    hook_config_result = spy.call_args.kwargs["hook_config_result"]
    if hook_config_result is None:
        return None
    assert isinstance(hook_config_result, HookConfigResult)
    return hook_config_result


async def _new_session(backend: FakeBackend) -> MagicMock:
    spy, patched_loop = _spy_agent_loop(backend)
    acp = _create_acp_agent()
    with patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched_loop):
        await acp.new_session(cwd=str(Path.cwd()), mcp_servers=[])
    spy.assert_called_once()
    return spy


async def _load_session(
    backend: FakeBackend,
    tmp_path: Path,
    *,
    session_id: str = "session-id",
    loaded_messages: list[LLMMessage] | None = None,
) -> MagicMock:
    spy, patched_loop = _spy_agent_loop(backend)
    acp = _create_acp_agent()
    session_dir = tmp_path / "sessions" / session_id
    session_dir.mkdir(parents=True)
    disk_metadata = SessionMetadata(
        session_id=session_id,
        start_time="2024-01-01T12:00:00Z",
        end_time="2024-01-01T12:05:00Z",
        git_commit=None,
        git_branch=None,
        environment={"working_directory": str(Path.cwd())},
        username="test-user",
    )
    (session_dir / "meta.json").write_text(disk_metadata.model_dump_json())
    loader_result = (loaded_messages or [], {"session_id": session_id})

    with (
        patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=patched_loop),
        patch(
            "vibe.acp.acp_agent_loop.SessionLoader.find_session_by_id",
            return_value=session_dir,
        ),
        patch(
            "vibe.acp.acp_agent_loop.SessionLoader.load_session",
            return_value=loader_result,
        ),
    ):
        await acp.load_session(
            cwd=str(Path.cwd()), session_id=session_id, mcp_servers=[]
        )
    spy.assert_called_once()
    return spy


@pytest.mark.asyncio
class TestAcpHooksLoading:
    async def test_new_session_hooks_enabled_loads_valid_hook(
        self, backend: FakeBackend, config_dir: Path
    ) -> None:
        _write_config(config_dir, enable_hooks=True)
        (config_dir / "hooks.toml").write_text(
            '[[hooks]]\nname = "lint"\ntype = "post_agent_turn"\ncommand = "eslint ."\n'
        )

        spy = await _new_session(backend)

        result = _hook_config_from_spy(spy)
        assert result is not None
        assert len(result.hooks) == 1
        assert result.hooks[0].name == "lint"
        assert result.hooks[0].type == HookType.POST_AGENT_TURN
        assert result.hooks[0].command == "eslint ."
        assert result.hooks[0].timeout == 30.0
        assert result.issues == []

    async def test_new_session_hooks_enabled_invalid_toml(
        self, backend: FakeBackend, config_dir: Path
    ) -> None:
        _write_config(config_dir, enable_hooks=True)
        (config_dir / "hooks.toml").write_text("{{broken toml")

        spy = await _new_session(backend)

        result = _hook_config_from_spy(spy)
        assert result is not None
        assert result.hooks == []
        assert len(result.issues) == 1
        assert "Failed to parse" in result.issues[0].message

    async def test_load_session_hooks_enabled_loads_valid_hook(
        self, backend: FakeBackend, config_dir: Path, tmp_path: Path
    ) -> None:
        _write_config(config_dir, enable_hooks=True)
        (config_dir / "hooks.toml").write_text(
            '[[hooks]]\nname = "lint"\ntype = "post_agent_turn"\ncommand = "eslint ."\n'
        )

        spy = await _load_session(
            backend,
            tmp_path,
            loaded_messages=[
                LLMMessage(role=Role.system, content="system"),
                LLMMessage(role=Role.user, content="hello"),
            ],
        )

        result = _hook_config_from_spy(spy)
        assert result is not None
        assert len(result.hooks) == 1
        assert result.hooks[0].name == "lint"
        assert result.hooks[0].type == HookType.POST_AGENT_TURN
        assert result.hooks[0].command == "eslint ."
        assert result.hooks[0].timeout == 30.0
        assert result.issues == []
