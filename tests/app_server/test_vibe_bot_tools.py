from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
import sys
from typing import Any

import httpx2
import pytest

from tests.app_server.test_vibe_bot import (
    BOT,
    ORCH,
    VibeBotCallRefused,
    _call,
    _controller,
    _FakeClient,
    _FakeSessionApi,
    _follow,
    _until,
)
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._vibe_bot import VibeBotLocalSessions
from vibe.app_server._vibe_bot_tools import (
    MAX_LIST_BYTES,
    HarnessLocalMCP,
    LocalMCP,
    LocalToolDescriptor,
    LocalToolFailure,
    LocalToolResult,
    LocalTools,
    local_tool_mode,
)
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.core.config import VibeConfigSchema
from vibe.core.config.models import MCPStdio

FAKE_SERVER = Path(__file__).parent / "fixtures" / "fake_local_mcp_server.py"
ECHO = LocalToolDescriptor(
    name="echo", description="Echo.", input_schema={"type": "object"}, read_only=True
)
WRITE = LocalToolDescriptor(
    name="write_note",
    description="Write.",
    input_schema={"type": "object"},
    read_only=False,
)


@pytest.mark.parametrize(
    ("entry", "ceiling", "read_only", "expected"),
    [
        ("allow", "ask", True, "ask"),
        ("allow", "accept-edits", True, "ask"),
        ("allow", "smart-approve", True, "allow"),
        ("allow", "smart-approve", False, "ask"),
        ("allow", "auto-approve", False, "allow"),
        ("ask", "auto-approve", True, "ask"),
        ("allow", "my-custom-policy", True, "ask"),
    ],
)
def test_a_local_tool_runs_the_strictest_of_its_entry_and_the_ceiling(
    entry: Any, ceiling: str, read_only: bool, expected: str
) -> None:
    assert local_tool_mode(entry, ceiling, read_only=read_only) == expected


class _FakeMCP(LocalMCP):
    def __init__(
        self,
        tools: dict[str, tuple[LocalToolDescriptor, ...]],
        *,
        answer: str = "echo: hi",
        delay: float = 0.0,
    ) -> None:
        self.tools = tools
        self.answer = answer
        self.delay = delay
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    async def list_tools(
        self, servers: Sequence[str], *, refresh: bool
    ) -> dict[str, tuple[LocalToolDescriptor, ...]]:
        return {s: self.tools[s] for s in servers if s in self.tools}

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        require_read_only: bool = False,
    ) -> LocalToolResult:
        """As `HarnessLocalMCP`: the deadline covers the run only."""
        self.calls.append((server, tool, arguments))
        try:
            await asyncio.wait_for(asyncio.sleep(self.delay), timeout_s)
        except TimeoutError as exc:
            raise LocalToolFailure(
                "local_tool_timeout",
                f"`{server}.{tool}` did not answer within {timeout_s:g} s",
            ) from exc
        return LocalToolResult(text=self.answer)

    async def aclose(self) -> None:
        pass


def _config(**kwargs: Any) -> VibeConfigSchema:
    return build_test_vibe_config(
        mcp_servers=[
            MCPStdio(name="grafana", transport="stdio", command="grafana-mcp"),
            MCPStdio(name="shell", transport="stdio", command="shell-mcp"),
        ],
        **kwargs,
    )


def _tools(config: VibeConfigSchema, mcp: LocalMCP) -> LocalTools:
    async def read_config() -> VibeConfigSchema:
        return config

    async def make_mcp() -> LocalMCP:
        return mcp

    return LocalTools(read_config=read_config, mcp=make_mcp)


class _ClaimingClient(_FakeClient):
    def __init__(self, *, taken: bool = False) -> None:
        super().__init__()
        self.taken = taken

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        await super().report(bot, event)
        return self.taken and event["kind"] == "tool_claimed"


@pytest.fixture
def sessions(tmp_path: Path) -> VibeBotLocalSessions:
    return VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")


async def _answers(
    sessions: VibeBotLocalSessions,
    config: VibeConfigSchema,
    mcp: _FakeMCP,
    calls: list[dict[str, Any]],
    *,
    client: _FakeClient | None = None,
    settle: float = 0.0,
) -> tuple[list[dict[str, Any]], _FakeClient]:
    api = _FakeSessionApi(calls)
    client = client or _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: len(api.answers) == len(calls))
        await asyncio.sleep(settle)
        order = [c["callback"]["id"] for c in calls]
        return sorted(api.answers, key=lambda a: order.index(a["callbackId"])), client
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_nothing_is_exposed_by_default(sessions: VibeBotLocalSessions) -> None:
    mcp = _FakeMCP({"grafana": (ECHO,)})
    machine = sessions.client_id(BOT)
    [listed, called], _ = await _answers(
        sessions,
        _config(vibe_bot_max_approval_policy="auto-approve"),
        mcp,
        [
            _call("cb-1", "list_local_tools", {"machine": machine}),
            _call(
                "cb-2",
                "call_local_tool",
                {"server": "grafana", "tool": "echo", "machine": machine},
            ),
        ],
    )

    assert listed["output"] == {
        "tools": [],
        "unreachableServers": [],
        "truncated": False,
    }
    assert called["error"]["code"] == "not_exposed"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_the_list_shows_only_the_allowlisted_tools_without_the_servers_secrets(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[
            {"server": "grafana", "tools": ["echo", "write_*"], "approval": "allow"}
        ],
    )
    mcp = _FakeMCP({"grafana": (ECHO, WRITE), "shell": (ECHO,)})
    [answer], client = await _answers(
        sessions, config, mcp, [_call("cb-1", "list_local_tools", {})]
    )

    machine = sessions.client_id(BOT)
    assert answer["output"] == {
        "tools": [
            {
                "machine": machine,
                "server": "grafana",
                "tool": "echo",
                "description": "Echo.",
                "inputSchema": {"type": "object"},
                "readOnly": True,
                "approval": "allow",
            },
            {
                "machine": machine,
                "server": "grafana",
                "tool": "write_note",
                "description": "Write.",
                "inputSchema": {"type": "object"},
                "readOnly": False,
                "approval": "ask",
            },
        ],
        "unreachableServers": [],
        "truncated": False,
    }
    assert "grafana-mcp" not in str(answer)
    assert client.reports == [
        {
            "kind": "tool_claimed",
            "clientId": machine,
            "requestId": "cb-1",
            "callerSessionId": ORCH,
            "tool": "list_local_tools",
            "server": None,
            "machine": None,
            "serves": True,
            "claimNonce": client.reports[0]["claimNonce"],
        }
    ]


@pytest.mark.asyncio
async def test_an_allowed_call_runs_once_and_answers_its_text(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    [answer], _ = await _answers(
        sessions,
        config,
        mcp,
        [
            _call(
                "cb-1",
                "call_local_tool",
                {"server": "grafana", "tool": "echo", "arguments": {"text": "hi"}},
            )
        ],
    )

    assert mcp.calls == [("grafana", "echo", {"text": "hi"})]
    assert answer["output"] == {
        "machine": sessions.client_id(BOT),
        "server": "grafana",
        "tool": "echo",
        "content": "echo: hi",
        "isError": False,
        "truncated": False,
        "totalBytes": 8,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ceiling", "tool", "approval"),
    [
        ("ask", "echo", "allow"),
        ("accept-edits", "echo", "allow"),
        ("smart-approve", "write_note", "allow"),
        ("auto-approve", "echo", "ask"),
    ],
)
async def test_a_call_that_needs_a_human_is_refused_and_never_runs(
    sessions: VibeBotLocalSessions, ceiling: str, tool: str, approval: str
) -> None:
    """Untrusted text can make the bot call a tool; the user's machine decides."""
    config = _config(
        vibe_bot_max_approval_policy=ceiling,
        vibe_bot_local_tools=[{"server": "grafana", "approval": approval}],
    )
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    [answer], _ = await _answers(
        sessions,
        config,
        mcp,
        [_call("cb-1", "call_local_tool", {"server": "grafana", "tool": tool})],
    )

    assert mcp.calls == []
    assert answer["error"]["code"] == "approval_required"


@pytest.mark.asyncio
async def test_a_tool_not_on_the_allowlist_is_refused_on_a_named_machine(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[
            {"server": "grafana", "tools": ["echo"], "approval": "allow"}
        ],
    )
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    [answer], _ = await _answers(
        sessions,
        config,
        mcp,
        [
            _call(
                "cb-1",
                "call_local_tool",
                {
                    "server": "grafana",
                    "tool": "write_note",
                    "machine": sessions.client_id(BOT),
                },
            )
        ],
    )

    assert mcp.calls == []
    assert answer["error"]["code"] == "not_exposed"


@pytest.mark.asyncio
async def test_a_call_to_a_server_this_machine_does_not_expose_is_left_to_one_that_does(
    sessions: VibeBotLocalSessions,
) -> None:
    """The workflow knows which connected machine exposes it: it hands the call
    to that one and tells this one it is a duplicate.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana"}])
    mcp = _FakeMCP({"shell": (ECHO,)})
    api = _FakeSessionApi([
        _call("cb-1", "call_local_tool", {"server": "shell", "tool": "echo"})
    ])
    client = _ClaimingClient(taken=True)
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: bool(client.reports))
        await asyncio.sleep(0.05)

        [claim] = client.reports
        assert (claim["server"], claim["serves"]) == ("shell", False)
        assert api.answers == [] and mcp.calls == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_input", "code"),
    [
        ({"server": "shell", "tool": "echo"}, "unknown_server"),
        (
            {"server": "grafana", "tool": "echo", "machine": "gone"},
            "local_host_unreachable",
        ),
    ],
)
async def test_a_call_no_connected_machine_serves_fails_at_once(
    sessions: VibeBotLocalSessions, tool_input: dict[str, Any], code: str
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,), "shell": (ECHO,)})
    [answer], _ = await _answers(
        sessions, config, mcp, [_call("cb-1", "call_local_tool", tool_input)]
    )

    assert answer["error"]["code"] == code and mcp.calls == []


@pytest.mark.asyncio
async def test_a_call_another_machine_claimed_first_never_runs_here(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    api = _FakeSessionApi([
        _call("cb-1", "call_local_tool", {"server": "grafana", "tool": "echo"})
    ])
    client = _ClaimingClient(taken=True)
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: bool(client.reports))
        await asyncio.sleep(0.1)

        assert mcp.calls == [] and api.answers == []
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


class _RefusingClient(_FakeClient):
    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        await super().report(bot, event)
        raise VibeBotCallRefused(
            "not_allowed_for_role: the user's local tools are only available to the orchestrator"
        )


@pytest.mark.asyncio
async def test_a_claim_the_bot_refuses_fails_at_once_with_its_code_and_never_runs(
    sessions: VibeBotLocalSessions,
) -> None:
    """A worker's call: the workflow refuses it, so it fails now rather than after the grace."""
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    answers, _ = await _answers(
        sessions,
        config,
        mcp,
        [
            _call("cb-1", "call_local_tool", {"server": "grafana", "tool": "echo"}),
            _call("cb-2", "list_local_tools", {}),
        ],
        client=_RefusingClient(),
    )

    assert mcp.calls == []
    for answer in answers:
        assert answer["error"] == {
            "code": "not_allowed_for_role",
            "message": "the user's local tools are only available to the orchestrator",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "tool_input"),
    [
        ("call_local_tool", {"server": "g" * 257, "tool": "echo"}),
        ("call_local_tool", {"server": "grafana", "tool": "e" * 257}),
        (
            "call_local_tool",
            {"server": "grafana", "tool": "echo", "machine": "m" * 257},
        ),
        ("list_local_tools", {"server": "g" * 257}),
        ("list_local_tools", {"machine": "m" * 257}),
    ],
)
async def test_a_name_over_256_characters_fails_before_the_bot_hears_of_the_call(
    sessions: VibeBotLocalSessions, name: str, tool_input: dict[str, Any]
) -> None:
    """Felix on #81713: the bot's schema caps names, but only this machine reads the input."""
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    [answer], client = await _answers(
        sessions, config, mcp, [_call("cb-1", name, tool_input)]
    )

    assert answer["error"]["code"] == "invalid_input"
    assert [r for r in client.reports if r["kind"] == "tool_claimed"] == []
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_arguments_over_32_kb_fail_before_the_bot_hears_of_the_call(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81713: the bot's schema caps arguments, but only this machine reads them."""
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    big = {"server": "grafana", "tool": "echo", "arguments": {"text": "x" * 32 * 1024}}
    [answer], client = await _answers(
        sessions, config, mcp, [_call("cb-1", "call_local_tool", big)]
    )

    assert answer["error"]["code"] == "invalid_input"
    assert "32768 bytes" in answer["error"]["message"]
    assert [r for r in client.reports if r["kind"] == "tool_claimed"] == []
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_a_large_answer_is_cut_to_the_users_cap_and_says_so(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tool_max_bytes=1024,
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)}, answer="é" * 4000)
    [answer], _ = await _answers(
        sessions,
        config,
        mcp,
        [_call("cb-1", "call_local_tool", {"server": "grafana", "tool": "echo"})],
    )

    output = answer["output"]
    assert output["truncated"] is True and output["totalBytes"] == 8000
    assert len(output["content"].encode()) <= 1024


@pytest.mark.asyncio
async def test_a_call_that_outlasts_its_timeout_fails_with_its_own_code(
    sessions: VibeBotLocalSessions,
) -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name="grafana",
                transport="stdio",
                command="grafana-mcp",
                tool_timeout_sec=0.05,
            )
        ],
        vibe_bot_max_approval_policy="auto-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)}, delay=5)
    [answer], _ = await _answers(
        sessions,
        config,
        mcp,
        [_call("cb-1", "call_local_tool", {"server": "grafana", "tool": "echo"})],
    )

    assert answer["error"]["code"] == "local_tool_timeout"


@pytest.mark.asyncio
async def test_the_summary_names_the_exposed_servers_and_changes_with_the_config() -> (
    None
):
    mcp = _FakeMCP({})
    first = await _tools(
        _config(vibe_bot_local_tools=[{"server": "grafana"}, {"server": "missing"}]),
        mcp,
    ).summary()
    second = await _tools(
        _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}]), mcp
    ).summary()

    assert [s.name for s in first.servers] == ["grafana"]
    assert first.digest != second.digest


@pytest.mark.asyncio
async def test_a_real_stdio_server_is_listed_and_called_through_the_harness(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name="fake",
                transport="stdio",
                command=[sys.executable, str(FAKE_SERVER)],
            )
        ],
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tool_max_bytes=100,
        vibe_bot_local_tools=[{"server": "fake", "approval": "allow"}],
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)

    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    async def make_mcp() -> LocalMCP:
        return HarnessLocalMCP(
            catalog=catalog, authorization=authentication, cache_root=tmp_path
        )

    async def read_config() -> VibeConfigSchema:
        return config

    tools = LocalTools(read_config=read_config, mcp=make_mcp)
    try:
        listed = await tools.list({}, "m")
        modes = {t["tool"]: t["approval"] for t in listed["tools"]}
        mode = await tools.mode("fake", "big")
        result = await tools.run(
            "fake", "big", {"size": 500}, max_bytes=mode[1], timeout_s=mode[2]
        )
    finally:
        await tools.aclose()

    assert modes == {
        "big": "allow",
        "echo": "allow",
        "slow": "allow",
        "write_note": "ask",
    }
    assert mode == ("allow", 100, 60.0)
    assert result == {
        "content": "x" * 100,
        "isError": False,
        "truncated": True,
        "totalBytes": 500,
    }


@pytest.mark.asyncio
async def test_a_call_to_one_server_keeps_the_others_connected(tmp_path: Path) -> None:
    """Calls run concurrently: one to another server must not close this one's."""
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name=name, transport="stdio", command=[sys.executable, str(FAKE_SERVER)]
            )
            for name in ("one", "two")
        ]
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)
    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    mcp = HarnessLocalMCP(
        catalog=catalog, authorization=authentication, cache_root=tmp_path
    )
    try:
        await mcp.call_tool("one", "echo", {"text": "a"}, timeout_s=10)
        await mcp.list_tools(["one"], refresh=False)
        await mcp.call_tool("two", "echo", {"text": "b"}, timeout_s=10)
        assert mcp._runtime is not None
        routed = {
            r.descriptor.server_name for r in mcp._runtime.snapshot.routes.values()
        }
    finally:
        await mcp.aclose()

    assert routed == {"one", "two"}


class _FlakyAnswerApi(_FakeSessionApi):
    def __init__(self, callbacks: list[dict[str, Any]], failures: int) -> None:
        super().__init__(callbacks)
        self.failures = failures

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.method == "POST" and self.failures > 0:
            self.failures -= 1
            return httpx2.Response(503)
        return super().handler(request)


@pytest.mark.asyncio
async def test_a_result_the_session_did_not_take_is_sent_again_without_running_twice(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    mcp = _FakeMCP({"grafana": (ECHO,)})
    api = _FlakyAnswerApi(
        [
            _call(
                "cb-1",
                "call_local_tool",
                {"server": "grafana", "tool": "echo", "arguments": {"text": "hi"}},
            )
        ],
        failures=1,
    )
    client = _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: len(api.answers) == 1)
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "echo", {"text": "hi"})]
    assert api.answers[0]["output"]["content"] == "echo: hi"
    assert [e["kind"] for e in client.reports] == ["tool_claimed"]


@pytest.mark.asyncio
async def test_an_entry_names_its_server_as_written_in_mcp_servers(
    sessions: VibeBotLocalSessions,
) -> None:
    config = build_test_vibe_config(
        mcp_servers=[MCPStdio(name="my grafana", transport="stdio", command=["true"])],
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "my grafana", "approval": "allow"}],
    )
    tools = _tools(config, _FakeMCP({"my_grafana": (ECHO,)}))

    listed = await tools.list({}, "m")

    assert [(t["server"], t["tool"]) for t in listed["tools"]] == [
        ("my_grafana", "echo")
    ]


@pytest.mark.asyncio
async def test_a_call_retunes_only_its_own_server(tmp_path: Path) -> None:
    """Another call's timeout or listing must not reach a server in use."""
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name=name,
                transport="stdio",
                command=[sys.executable, str(FAKE_SERVER)],
                tool_timeout_sec=timeout,
            )
            for name, timeout in (("one", 600), ("two", 5))
        ]
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)
    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    mcp = HarnessLocalMCP(
        catalog=catalog, authorization=authentication, cache_root=tmp_path
    )
    try:
        await mcp.call_tool("one", "echo", {"text": "a"}, timeout_s=60)
        await mcp.call_tool("two", "echo", {"text": "b"}, timeout_s=5)
        await mcp.list_tools(["two"], refresh=True)
        assert mcp._runtime is not None
        timeouts = {
            s.name: s.tool_timeout_s
            for s in mcp._runtime._configuration.servers  # pyright: ignore[reportPrivateUsage]
        }
    finally:
        await mcp.aclose()

    assert timeouts == {"one": 60.0, "two": 5.0}


@pytest.mark.asyncio
async def test_a_call_to_an_allowlisted_server_that_is_down_says_so(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    tools = _tools(config, _FakeMCP({}))

    with pytest.raises(LocalToolFailure) as failure:
        await tools.mode("grafana", "echo")

    assert failure.value.code == "local_server_unavailable"


@pytest.mark.asyncio
async def test_concurrent_calls_share_one_runtime_even_when_its_factory_awaits() -> (
    None
):
    made: list[_FakeMCP] = []

    async def slow_factory() -> LocalMCP:
        await asyncio.sleep(0.05)
        made.append(_FakeMCP({"grafana": (ECHO,)}))
        return made[-1]

    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )

    async def read_config() -> VibeConfigSchema:
        return config

    tools = LocalTools(read_config=read_config, mcp=slow_factory)
    await asyncio.gather(tools.mode("grafana", "echo"), tools.mode("grafana", "echo"))

    assert len(made) == 1


@pytest.mark.asyncio
async def test_a_call_after_close_opens_no_runtime() -> None:
    """Bugbot on #81689: an answer still running at teardown must not mint a runtime nobody closes."""
    made: list[_FakeMCP] = []

    async def factory() -> LocalMCP:
        made.append(_FakeMCP({"grafana": (ECHO,)}))
        return made[-1]

    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )

    async def read_config() -> VibeConfigSchema:
        return config

    tools = LocalTools(read_config=read_config, mcp=factory)
    await tools.aclose()

    with pytest.raises(LocalToolFailure) as failure:
        await tools.mode("grafana", "echo")

    assert failure.value.code == "local_tools_unavailable"
    assert made == []


@pytest.mark.asyncio
async def test_closing_the_controller_closes_the_mcp_runtime_even_when_cancelled(
    sessions: VibeBotLocalSessions,
) -> None:
    """Benjamin on #81689: the app server cancels every task `close()` returns before awaiting it."""
    from vibe.app_server._execution import cancel_tasks

    closed: list[bool] = []

    class _Tracked(_FakeMCP):
        async def aclose(self) -> None:
            closed.append(True)

    mcp = _Tracked({"grafana": (ECHO,)})
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    tools = _tools(config, mcp)
    controller = _controller(
        _FakeSessionApi([]), _ClaimingClient(), sessions, local_tools=tools
    )
    await _follow(controller, session_ids=[])
    await tools.mode("grafana", "echo")

    await cancel_tasks(controller.close(), label="test")
    await controller.aclose_tools()

    assert closed == [True]


@pytest.mark.asyncio
async def test_a_detach_drops_the_runtime_and_the_next_call_opens_a_new_one(
    sessions: VibeBotLocalSessions,
) -> None:
    made: list[_FakeMCP] = []

    async def factory() -> LocalMCP:
        made.append(_FakeMCP({"grafana": (ECHO,)}))
        return made[-1]

    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )

    async def read_config() -> VibeConfigSchema:
        return config

    tools = LocalTools(read_config=read_config, mcp=factory)
    await tools.mode("grafana", "echo")
    await tools.drop_runtime()
    await tools.mode("grafana", "echo")

    assert len(made) == 2


@pytest.mark.asyncio
async def test_the_list_answer_stays_under_the_size_cap(
    sessions: VibeBotLocalSessions,
) -> None:
    """Felix on #81689: the list carries schemas, so it needs a cap like a call's answer."""
    big = [
        LocalToolDescriptor(
            name=f"t{i}",
            description="d" * 2000,
            input_schema={"type": "object", "description": "s" * 4000},
            read_only=True,
        )
        for i in range(40)
    ]
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    tools = _tools(config, _FakeMCP({"grafana": tuple(big)}))

    listed = await tools.list({}, "m")

    assert len(json.dumps(listed).encode()) <= MAX_LIST_BYTES
    assert listed["truncated"] is True
    assert 0 < len(listed["tools"]) < 40


@pytest.mark.asyncio
async def test_the_list_leaves_out_tools_the_user_turned_off(tmp_path: Path) -> None:
    """Benjamin on #81689: `disabled_tools` hides a tool from the bot as from a session."""
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name="fake",
                transport="stdio",
                command=[sys.executable, str(FAKE_SERVER)],
                disabled_tools=["slow"],
            )
        ],
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "fake", "approval": "allow"}],
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)
    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    async def make_mcp() -> LocalMCP:
        return HarnessLocalMCP(
            catalog=catalog, authorization=authentication, cache_root=tmp_path
        )

    async def read_config() -> VibeConfigSchema:
        return config

    tools = LocalTools(read_config=read_config, mcp=make_mcp)
    try:
        listed = await tools.list({}, "m")
        with pytest.raises(LocalToolFailure) as failure:
            await tools.mode("fake", "slow")
    finally:
        await tools.aclose()

    assert "slow" not in {t["tool"] for t in listed["tools"]}
    assert failure.value.code == "unknown_tool"


@pytest.mark.asyncio
async def test_a_call_waiting_behind_a_listing_is_not_reported_as_a_tool_timeout(
    sessions: VibeBotLocalSessions,
) -> None:
    """Benjamin on #81689: only `execute` counts against the tool's deadline."""

    class _SlowToOpen(_FakeMCP):
        async def call_tool(
            self,
            server: str,
            tool: str,
            arguments: dict[str, Any],
            *,
            timeout_s: float,
            require_read_only: bool = False,
        ) -> LocalToolResult:
            await asyncio.sleep(0.3)
            return await super().call_tool(server, tool, arguments, timeout_s=timeout_s)

    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    tools = _tools(config, _SlowToOpen({"grafana": (ECHO,)}))

    result = await tools.run(
        "grafana", "echo", {"text": "hi"}, max_bytes=100, timeout_s=0.1
    )

    assert result["content"] == "echo: hi"


@pytest.mark.asyncio
async def test_a_real_server_that_outlasts_the_deadline_times_out(
    tmp_path: Path,
) -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name="fake",
                transport="stdio",
                command=[sys.executable, str(FAKE_SERVER)],
            )
        ]
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)
    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    mcp = HarnessLocalMCP(
        catalog=catalog, authorization=authentication, cache_root=tmp_path
    )
    try:
        await mcp.call_tool("fake", "echo", {"text": "warm"}, timeout_s=10)
        with pytest.raises(LocalToolFailure) as failure:
            await mcp.call_tool("fake", "slow", {"seconds": 3}, timeout_s=0.5)
    finally:
        await mcp.aclose()

    assert failure.value.code == "local_tool_timeout"


@pytest.mark.asyncio
async def test_an_allow_that_rests_on_read_only_is_checked_on_the_route_that_runs(
    tmp_path: Path,
) -> None:
    """Gabriel on #81689: a cached read-only claim must not run a tool that is no longer read-only."""
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStdio(
                name="fake",
                transport="stdio",
                command=[sys.executable, str(FAKE_SERVER)],
            )
        ]
    )
    authentication = MCPAuthenticationService()
    catalog_service = MCPCatalogService(authentication)
    orchestrator = FakeConfigOrchestrator(config)

    async def catalog(owner: object) -> Any:
        return await catalog_service.resolve_catalog(orchestrator, owner=owner)

    mcp = HarnessLocalMCP(
        catalog=catalog, authorization=authentication, cache_root=tmp_path
    )
    try:
        with pytest.raises(LocalToolFailure) as failure:
            await mcp.call_tool(
                "fake",
                "write_note",
                {"text": "x"},
                timeout_s=10,
                require_read_only=True,
            )
        ran = await mcp.call_tool(
            "fake", "echo", {"text": "ok"}, timeout_s=10, require_read_only=True
        )
    finally:
        await mcp.aclose()

    assert failure.value.code == "approval_required"
    assert ran.text == "echo: ok"


@pytest.mark.asyncio
async def test_each_app_server_claims_with_its_own_nonce_and_a_retry_keeps_it(
    sessions: VibeBotLocalSessions,
) -> None:
    """Benjamin on #81689: a restart or a reconnect must not look like a retry."""
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    nonces: list[str] = []
    for _ in range(2):
        _, client = await _answers(
            sessions,
            config,
            _FakeMCP({"grafana": (ECHO,)}),
            [
                _call(
                    "cb-1",
                    "call_local_tool",
                    {"server": "grafana", "tool": "echo", "arguments": {}},
                )
            ],
        )
        [claim] = [r for r in client.reports if r["kind"] == "tool_claimed"]
        nonces.append(claim["claimNonce"])

    assert all(nonces) and nonces[0] != nonces[1]


@pytest.mark.asyncio
async def test_a_detach_renews_the_claim_nonce(sessions: VibeBotLocalSessions) -> None:
    controller = _controller(_FakeSessionApi([]), _ClaimingClient(), sessions)
    before = controller._claim_nonce  # pyright: ignore[reportPrivateUsage]

    await controller.drop_tools()

    assert controller._claim_nonce != before  # pyright: ignore[reportPrivateUsage]


class _FailingToStart(_FakeMCP):
    """A server whose start fails with text that names the machine's command and paths."""

    async def list_tools(
        self, servers: Sequence[str], *, refresh: bool
    ) -> dict[str, tuple[LocalToolDescriptor, ...]]:
        raise RuntimeError(
            "spawn /Users/me/bin/grafana-mcp --token-file /Users/me/.secret failed"
        )

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        require_read_only: bool = False,
    ) -> LocalToolResult:
        raise RuntimeError(
            "spawn /Users/me/bin/grafana-mcp --token-file /Users/me/.secret failed"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["list_local_tools", "call_local_tool"])
async def test_a_server_that_fails_to_start_tells_the_bot_nothing_about_this_machine(
    sessions: VibeBotLocalSessions, tool: str
) -> None:
    """Bugbot on #81689: startup failures must not carry commands, paths or URLs to the cloud."""
    config = _config(
        vibe_bot_max_approval_policy="smart-approve",
        vibe_bot_local_tools=[{"server": "grafana", "approval": "allow"}],
    )
    [answer], _ = await _answers(
        sessions,
        config,
        _FailingToStart({"grafana": (ECHO,)}),
        [
            _call(
                "cb-1",
                tool,
                {"server": "grafana", "tool": "echo", "arguments": {}}
                if tool == "call_local_tool"
                else {},
            )
        ],
    )

    text = json.dumps(answer)
    assert "/Users/me" not in text and "--token-file" not in text
    assert answer["error"]["code"] == "local_tool_failed"


@pytest.mark.asyncio
async def test_a_machine_without_local_tools_answers_an_empty_summary(
    sessions: VibeBotLocalSessions,
) -> None:
    """Felix on #81689: Desktop's heartbeat asks every app server, local tools or not."""
    controller = _controller(_FakeSessionApi([]), _ClaimingClient(), sessions)

    summary = await controller.dispatch("vibebot/localTools", {"botWorkflowId": BOT})

    assert summary.model_dump(by_alias=True)["servers"] == []


@pytest.mark.asyncio
async def test_a_config_that_fails_to_load_tells_the_bot_nothing_about_this_machine(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81689: reading the config before the claim must not leak its paths either."""

    async def read_config() -> VibeConfigSchema:
        raise OSError("cannot read /Users/me/.vibe/config.toml")

    async def make_mcp() -> LocalMCP:
        return _FakeMCP({"grafana": (ECHO,)})

    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "echo", "arguments": {}},
        )
    ])
    controller = _controller(
        api,
        _ClaimingClient(),
        sessions,
        local_tools=LocalTools(read_config=read_config, mcp=make_mcp),
    )
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    [answer] = api.answers
    assert "/Users/me" not in json.dumps(answer)
    assert answer["error"]["code"] == "local_tool_failed"
