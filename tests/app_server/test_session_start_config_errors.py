"""A session start with a broken config says how to fix it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
import pytest_asyncio

from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AgentConfig,
    AppServerResponseError,
    ClientInfo,
    ProtocolErrorCode,
    SessionListParams,
    SessionStartParams,
)
from vibe.app_server.transport import memory_transport_pair

_CONFIG = """
active_model = "bench"
include_project_context = false
[experiments]
enable = false
[telemetry]
enable = false
[[providers]]
name = "fake"
api_base = "http://127.0.0.1:1/v1"
api_key_env_var = "TEST_FAKE_API_KEY"
api_style = "openai"
[[models]]
name = "bench"
provider = "fake"
alias = "bench"
thinking = "off"
"""


@pytest.fixture
def write_config(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., None]:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("TEST_FAKE_API_KEY", "test")

    # Top-level keys go first: after the tables they would land in the last one.
    def write(settings: str, *, top_level: str = "") -> None:
        config = top_level + _CONFIG + settings
        (config_dir / "config.toml").write_text(config, encoding="utf-8")

    return write


@pytest_asyncio.fixture
async def client(process_services: ProcessServices) -> AsyncIterator[AppServerClient]:
    client_transport, server_transport = memory_transport_pair()
    process = HarnessProcess(services=process_services)
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        await client.initialize(ClientInfo(name="test", version="1"))
        await client.notify("initialized")
        yield client
    finally:
        await client.close()
        await process.close()


@pytest.mark.parametrize(
    ("tool_settings", "message"),
    [
        pytest.param(
            "default_timeout = 60\nmax_timeout_seconds = inf\n",
            "max_timeout_seconds inf is not a number of seconds",
            id="maximum not a number of seconds",
        ),
        pytest.param(
            'default_timeout = 60\nmax_timeout_seconds = "inf"\n',
            "max_timeout_seconds inf is not a number of seconds",
            id="quoted maximum not a number of seconds",
        ),
        pytest.param(
            "default_timeout = inf\nmax_timeout_seconds = 600\n",
            "default_timeout inf is not a number of seconds",
            id="default not a number of seconds",
        ),
        pytest.param(
            "default_timeout = 600\n",
            "tools.bash: default_timeout is set without max_timeout_seconds."
            " Set both, or neither.",
            id="default set without the maximum",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_config_that_breaks_a_schema_rule_is_a_configuration_error(
    write_config: Callable[[str], None],
    client: AppServerClient,
    tool_settings: str,
    message: str,
) -> None:
    """*Prepare*: A config that loads but breaks a schema rule: a bash maximum
    timeout that is not a number of seconds, or a bash default timeout without a
    maximum.
    *Do*: Start a session.
    *Assert*: A configuration error saying what to fix, not a generic
    invalid-parameters error.
    """
    # Prepare
    write_config(f"[tools.bash]\n{tool_settings}")

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request("session/start", SessionStartParams())

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert message in error.message


@pytest.mark.asyncio
async def test_listing_sessions_with_a_config_that_breaks_a_schema_rule_is_a_configuration_error(
    write_config: Callable[[str], None], client: AppServerClient
) -> None:
    """*Prepare*: A config with a bash default timeout above Vibe's maximum and
    no maximum.
    *Do*: List the sessions.
    *Assert*: The configuration error a session start gives, so a client can
    say what to fix instead of "Internal server error".
    """
    # Prepare
    write_config("[tools.bash]\ndefault_timeout = 600\n")

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request("session/list", SessionListParams())

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert (
        "tools.bash: default_timeout is set without max_timeout_seconds."
        " Set both, or neither." in error.message
    )


@pytest.mark.asyncio
async def test_starting_an_agent_with_one_bash_timeout_is_a_configuration_error(
    config_dir: Path, write_config: Callable[[str], None], client: AppServerClient
) -> None:
    """*Prepare*: A valid config, and a custom agent file that sets a bash
    ``default_timeout`` without ``max_timeout_seconds``.
    *Do*: Start a session in that agent.
    *Assert*: The configuration error a config.toml with that setting gives,
    naming the agent file, not "Agent not found".
    """
    # Prepare
    write_config("")
    agents = config_dir / "agents"
    agents.mkdir()
    (agents / "slow.toml").write_text(
        "[tools.bash]\ndefault_timeout = 600\n", encoding="utf-8"
    )

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request(
            "session/start", SessionStartParams(agent_config=AgentConfig(agent="slow"))
        )

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert error.message == (
        "Cannot load the Vibe config."
        f" Agent 'slow' in {agents / 'slow.toml'}: tools: tools.bash: default_timeout"
        " is set without max_timeout_seconds. Set both, or neither."
    )


@pytest.mark.parametrize(
    ("agent", "message"),
    [
        pytest.param("typo", "Agent 'typo' not found.", id="unknown"),
        pytest.param(
            "plan",
            "Agent 'plan' was removed in Vibe 3.0. Use 'ask' or a custom agent.",
            id="removed builtin",
        ),
        pytest.param(
            "explore",
            "Agent 'explore' is a subagent and cannot be used as the primary agent."
            " Only agents of type 'agent' can be selected with --agent.",
            id="subagent",
        ),
    ],
)
@pytest.mark.asyncio
async def test_starting_an_agent_that_cannot_start_is_an_invalid_request(
    write_config: Callable[[str], None],
    client: AppServerClient,
    agent: str,
    message: str,
) -> None:
    # Prepare
    write_config("")

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request(
            "session/start", SessionStartParams(agent_config=AgentConfig(agent=agent))
        )

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data is None
    assert error.message == message


@pytest.mark.asyncio
async def test_starting_an_unavailable_agent_is_an_invalid_request(
    write_config: Callable[..., None], client: AppServerClient
) -> None:
    # Prepare
    write_config("", top_level="enable_auto_approve = false\n")

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request(
            "session/start",
            SessionStartParams(agent_config=AgentConfig(agent="auto-approve")),
        )

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.message == (
        "Agent 'auto-approve' is unavailable: the auto-approve mode is disabled"
        " ('enable_auto_approve' is false)."
    )


@pytest.mark.asyncio
async def test_a_default_agent_that_cannot_start_is_a_configuration_error(
    write_config: Callable[..., None], client: AppServerClient
) -> None:
    # Prepare
    write_config("", top_level='default_agent = "typo"\n')

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request("session/start", SessionStartParams())

    # Assert
    error = exc_info.value.error
    assert error.code is ProtocolErrorCode.INVALID_PARAMS
    assert error.data == {"kind": "configuration"}
    assert error.message == (
        "Cannot load the Vibe config. default_agent: Agent 'typo' not found."
    )


@pytest.mark.asyncio
async def test_a_disabled_default_agent_is_named_once(
    write_config: Callable[..., None], client: AppServerClient
) -> None:
    # Prepare
    write_config(
        "",
        top_level='default_agent = "accept-edits"\ndisabled_agents = ["accept-edits"]\n',
    )

    # Do
    with pytest.raises(AppServerResponseError) as exc_info:
        await client.request("session/start", SessionStartParams())

    # Assert
    assert exc_info.value.error.message == (
        "Cannot load the Vibe config. default_agent 'accept-edits' is in"
        " 'disabled_agents' ['accept-edits']. Remove 'accept-edits' from"
        " 'disabled_agents', or set 'default_agent' to an enabled agent."
    )
