from __future__ import annotations

import asyncio
from collections.abc import Sequence
import json
import os
from pathlib import Path
import sys
import time
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
    approval_text,
    args_hash,
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
    ("ceiling", "tool", "approval", "ceiling_allows"),
    [
        ("ask", "echo", "allow", False),
        ("accept-edits", "echo", "allow", False),
        ("smart-approve", "write_note", "allow", False),
        ("auto-approve", "echo", "ask", True),
    ],
)
async def test_a_call_that_needs_a_human_waits_for_the_inbox_and_does_not_run(
    sessions: VibeBotLocalSessions,
    ceiling: str,
    tool: str,
    approval: str,
    ceiling_allows: bool,
) -> None:
    """Untrusted text can make the bot call a tool; the user approves it in the inbox."""
    config = _config(
        vibe_bot_max_approval_policy=ceiling,
        vibe_bot_local_tools=[{"server": "grafana", "approval": approval}],
    )
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    [answer], client = await _answers(
        sessions,
        config,
        mcp,
        [
            _call(
                "cb-1",
                "call_local_tool",
                {"server": "grafana", "tool": tool, "arguments": {"text": "x"}},
            )
        ],
    )

    assert mcp.calls == []
    assert answer["output"] == {"status": "awaiting_approval", "approvalId": "cb-1"}
    [pending] = [r for r in client.reports if r["kind"] == "tool_pending"]
    assert pending == {
        "kind": "tool_pending",
        "clientId": sessions.client_id(BOT),
        "requestId": "cb-1",
        "callerSessionId": ORCH,
        "server": "grafana",
        "tool": tool,
        "arguments": {"text": "x"},
        "argsHash": args_hash({"text": "x"}),
        "readOnly": tool == "echo",
        # The card offers Always allow only when this ceiling alone lets a call run (VIB2-442).
        "context": {"ceilingAllows": ceiling_allows},
    }


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


async def _approve(
    sessions: VibeBotLocalSessions,
    config: VibeConfigSchema,
    mcp: _FakeMCP,
    delivery: dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
) -> _ClaimingClient:
    """The call waits for the user; then the heartbeat hands its answer over."""
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {
                "server": "grafana",
                "tool": "write_note",
                "arguments": arguments or {"text": "x"},
            },
        )
    ])
    client = _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
        await controller.dispatch(
            "vibebot/deliver", {"botWorkflowId": BOT, "deliveries": [delivery]}
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
        return client
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


def _approval(decision: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "kind": "tool_approval",
        "requestId": "approve-cb-1",
        "callbackId": "cb-1",
        "argsHash": args_hash(args or {"text": "x"}),
        "decision": decision,
    }


@pytest.mark.asyncio
async def test_an_approved_call_runs_once_with_the_arguments_the_user_saw(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted")
    client = await _approve(sessions, config, mcp, _approval("allow"))

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done == {
        "kind": "tool_result",
        "clientId": sessions.client_id(BOT),
        "requestId": "cb-1",
        "outcome": "ok",
        "content": "noted",
        "isError": False,
        "truncated": False,
        "totalBytes": 5,
    }


@pytest.mark.asyncio
async def test_an_approval_for_other_arguments_runs_nothing(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    client = await _approve(
        sessions, config, mcp, _approval("allow", {"text": "rm -rf"})
    )

    assert mcp.calls == []
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "failed" and "arguments" in done["error"]


@pytest.mark.asyncio
async def test_a_denied_call_is_reported_rejected_without_running(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    client = await _approve(sessions, config, mcp, _approval("deny"))

    assert mcp.calls == []
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "rejected"


@pytest.mark.asyncio
async def test_an_approval_after_the_user_removed_the_tool_from_the_list_runs_nothing(
    sessions: VibeBotLocalSessions,
) -> None:
    """The policy is read again when the approval arrives."""
    asked = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    removed = _config(vibe_bot_local_tools=[])
    configs = [asked]
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})

    async def read_config() -> VibeConfigSchema:
        return configs[-1]

    async def make_mcp() -> LocalMCP:
        return mcp

    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    tools = LocalTools(read_config=read_config, mcp=make_mcp)
    controller = _controller(api, client, sessions, local_tools=tools)
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
        configs.append(removed)
        await controller.dispatch(
            "vibebot/deliver",
            {"botWorkflowId": BOT, "deliveries": [_approval("allow")]},
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == []
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "failed"
    assert done["error"].startswith("not_exposed: ")


@pytest.mark.asyncio
async def test_an_approval_this_machine_never_held_or_already_ran_is_reported_expired(
    sessions: VibeBotLocalSessions,
) -> None:
    """A restart between running and reporting must not run the call twice."""
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    client = _ClaimingClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, local_tools=_tools(config, mcp)
    )
    try:
        await _follow(controller, session_ids=[])
        await controller.dispatch(
            "vibebot/deliver",
            {"botWorkflowId": BOT, "deliveries": [_approval("allow")]},
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == []
    assert [r["outcome"] for r in client.reports if r["kind"] == "tool_result"] == [
        "expired"
    ]


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
async def test_a_slow_approved_run_neither_holds_the_heartbeat_nor_reports_twice(
    sessions: VibeBotLocalSessions,
) -> None:
    """The heartbeat waits for `vibebot/deliver`, and the workflow sends the
    approval again until the result arrives.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.5)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    deliver = {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
        await asyncio.wait_for(controller.dispatch("vibebot/deliver", deliver), 0.2)
        await asyncio.wait_for(controller.dispatch("vibebot/deliver", deliver), 0.2)
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
        await asyncio.sleep(0.1)
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    assert [r["outcome"] for r in client.reports if r["kind"] == "tool_result"] == [
        "ok"
    ]


@pytest.mark.asyncio
async def test_an_approval_two_connections_receive_runs_once_and_reports_once(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: the hold is shared by every connection, so the run in
    flight must be too.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.5)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    first = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    second = _controller(
        _FakeSessionApi([]), client, sessions, local_tools=_tools(config, mcp)
    )
    deliver = {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    try:
        await _follow(first)
        await _until(lambda: bool(api.answers))
        await first.dispatch("vibebot/deliver", deliver)
        await second.dispatch("vibebot/deliver", deliver)
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
        await asyncio.sleep(0.6)
    finally:
        await asyncio.gather(*first.close(), *second.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    assert [r["outcome"] for r in client.reports if r["kind"] == "tool_result"] == [
        "ok"
    ]


@pytest.mark.asyncio
async def test_an_approval_two_app_servers_of_one_machine_receive_runs_once(
    tmp_path: Path,
) -> None:
    """Felix on #81890: the hold file and client id are per machine, so a second
    app server handed the same approval must leave the run to the first, not
    report it expired while it runs.
    """
    id_file = tmp_path / "vibe_bot_client_id"
    here = VibeBotLocalSessions(client_id_file=id_file)
    other = VibeBotLocalSessions(client_id_file=id_file)
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.5)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    first = _controller(api, client, here, local_tools=_tools(config, mcp))
    second = _controller(
        _FakeSessionApi([]), client, other, local_tools=_tools(config, mcp)
    )
    deliver = {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    try:
        await _follow(first)
        await _until(lambda: bool(api.answers))
        await first.dispatch("vibebot/deliver", deliver)
        await asyncio.sleep(0.1)
        await second.dispatch("vibebot/deliver", deliver)
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
        await asyncio.sleep(0.6)
    finally:
        await asyncio.gather(*first.close(), *second.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    assert [r["outcome"] for r in client.reports if r["kind"] == "tool_result"] == [
        "ok"
    ]


def test_two_app_servers_of_one_machine_keep_each_others_holds(tmp_path: Path) -> None:
    id_file = tmp_path / "vibe_bot_client_id"
    here = VibeBotLocalSessions(client_id_file=id_file)
    other = VibeBotLocalSessions(client_id_file=id_file)
    assert other.held_tool(BOT, "cb-0") is None

    here.hold_tool(BOT, "cb-1", {"server": "s", "tool": "t", "argsHash": "h1"})
    other.hold_tool(BOT, "cb-2", {"server": "s", "tool": "t", "argsHash": "h2"})
    here.update_tool(BOT, "cb-1", running=True)

    reopened = VibeBotLocalSessions(client_id_file=id_file)
    assert reopened.held_tool(BOT, "cb-1") is not None
    assert reopened.held_tool(BOT, "cb-2") is not None
    assert other.held_tool(BOT, "cb-1")["running"] is True  # pyright: ignore[reportOptionalSubscript]


def test_a_run_lock_is_taken_once_per_machine_and_old_ones_are_cleared(
    tmp_path: Path,
) -> None:
    id_file = tmp_path / "vibe_bot_client_id"
    here = VibeBotLocalSessions(client_id_file=id_file)
    other = VibeBotLocalSessions(client_id_file=id_file)
    lock = here.claim_run(BOT, "cb-1")
    assert lock is not None
    assert other.claim_run(BOT, "cb-1") is None
    here.release_run(lock)
    again = other.claim_run(BOT, "cb-1")
    assert again is not None
    other.release_run(again)

    runs = tmp_path / "vibe_bot_runs"
    old = time.time() - 8 * 24 * 3600
    for stale in runs.iterdir():
        os.utime(stale, (old, old))
    fresh = here.claim_run(BOT, "cb-2")
    assert fresh is not None
    assert len(list(runs.iterdir())) == 1
    here.release_run(fresh)


@pytest.mark.asyncio
async def test_an_outcome_whose_report_was_lost_is_reported_on_the_next_follow(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: once the workflow expires the call it stops sending the
    approval, so a held outcome must go out without one.
    """
    sessions.hold_tool(
        BOT, "cb-1", {"server": "grafana", "tool": "write_note", "argsHash": "h"}
    )
    sessions.update_tool(BOT, "cb-1", running=True)
    sessions.update_tool(BOT, "cb-1", outcome={"outcome": "ok", "content": "noted"})
    sessions.hold_tool(BOT, "cb-2", {"server": "grafana", "tool": "t", "argsHash": "h"})
    client = _ClaimingClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        await _follow(controller, session_ids=[])
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
        await _until(lambda: sessions.held_tool(BOT, "cb-1") is None)
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert [r for r in client.reports if r["kind"] == "tool_result"] == [
        {
            "kind": "tool_result",
            "clientId": sessions.client_id(BOT),
            "requestId": "cb-1",
            "outcome": "ok",
            "content": "noted",
        }
    ]
    assert sessions.held_tool(BOT, "cb-2") is not None


@pytest.mark.asyncio
async def test_a_desktop_reports_only_its_own_held_outcomes(
    sessions: VibeBotLocalSessions,
) -> None:
    """Another Desktop on this Vibe home holds its own calls: their outcomes are
    its to report, under its id, or the bot refuses them.
    """
    other = sessions.client_id(BOT, instance="/profiles/dev")
    sessions.hold_tool(
        BOT,
        "cb-other",
        {"server": "s", "tool": "t", "argsHash": "h", "clientId": other},
    )
    sessions.update_tool(BOT, "cb-other", outcome={"outcome": "ok", "content": "x"})
    client = _ClaimingClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, instance="/profiles/dev-port-5182"
    )
    try:
        await _follow(controller, session_ids=[])
        await asyncio.sleep(0.05)
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert [r for r in client.reports if r["kind"] == "tool_result"] == []
    assert sessions.held_tool(BOT, "cb-other") is not None


@pytest.mark.asyncio
async def test_a_flush_that_finds_the_outcome_already_reported_sends_nothing(
    sessions: VibeBotLocalSessions,
) -> None:
    """Reviewer on #81890: another app server may report and drop the outcome
    between the follow's read and the flush's claim; the flush must not report
    the call rejected then.
    """
    client = _ClaimingClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    try:
        controller._start_approved(BOT, {"callbackId": "cb-1", "flush": True})  # pyright: ignore[reportPrivateUsage]
        await _until(lambda: not controller._approved_runs)  # pyright: ignore[reportPrivateUsage]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert [r for r in client.reports if r["kind"] == "tool_result"] == []


def test_a_run_lock_works_where_utime_takes_no_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer on #81890: Windows' `os.utime` takes a path, not a descriptor."""
    real = os.utime

    def path_only(target: Any, *args: Any, **kwargs: Any) -> None:
        if isinstance(target, int):
            raise NotImplementedError("utime: fd unavailable on this platform")
        real(target, *args, **kwargs)

    monkeypatch.setattr(os, "utime", path_only)
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    lock = sessions.claim_run(BOT, "cb-1")
    assert lock is not None
    sessions.release_run(lock)


def test_a_running_mark_that_cannot_be_saved_fails_before_the_run(
    sessions: VibeBotLocalSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer on #81890: without its `running` mark a crash mid-run would let
    the next delivery run the call again.
    """
    sessions.hold_tool(BOT, "cb-1", {"server": "s", "tool": "t", "argsHash": "h"})

    def refuse(*_: Any, **__: Any) -> None:
        raise PermissionError("in use")

    monkeypatch.setattr("vibe.app_server._vibe_bot._write_atomically", refuse)
    with pytest.raises(OSError):
        sessions.update_tool(BOT, "cb-1", required=True, running=True)


@pytest.mark.asyncio
async def test_two_bots_approved_runs_with_one_callback_id_are_both_kept(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: one Desktop follows several bots, whose callback ids may meet."""
    client = _ClaimingClient()
    controller = _controller(_FakeSessionApi([]), client, sessions)
    hold = asyncio.Event()

    async def slow(bot: str, delivery: dict[str, Any]) -> None:
        await hold.wait()

    controller._report_approved = slow  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    try:
        controller._start_approved(BOT, {"callbackId": "cb-1"})  # pyright: ignore[reportPrivateUsage]
        controller._start_approved("other-bot", {"callbackId": "cb-1"})  # pyright: ignore[reportPrivateUsage]
        assert len(controller._approved_runs) == 2  # pyright: ignore[reportPrivateUsage]
        hold.set()
        await _until(lambda: not controller._approved_runs)  # pyright: ignore[reportPrivateUsage]
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)


@pytest.mark.asyncio
async def test_a_call_whose_arguments_the_inbox_cannot_show_in_full_is_refused(
    sessions: VibeBotLocalSessions,
) -> None:
    """The user approves only arguments they can read whole."""
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    [answer], client = await _answers(
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
                    "arguments": {"text": "x" * 3000},
                },
            )
        ],
    )

    assert mcp.calls == []
    assert answer["error"]["code"] == "arguments_too_long"
    assert [r["kind"] for r in client.reports] == ["tool_claimed"]


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


class _LosingReportClient(_ClaimingClient):
    """Loses the first `tool_result` report."""

    def __init__(self) -> None:
        super().__init__()
        self.lost = False

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        if event["kind"] == "tool_result" and not self.lost:
            self.lost = True
            raise RuntimeError("the BFF is down")
        return await super().report(bot, event)


@pytest.mark.asyncio
async def test_a_lost_result_report_is_sent_again_with_what_ran(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted")
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _LosingReportClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    deliver = {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
        await controller.dispatch("vibebot/deliver", deliver)
        await _until(lambda: client.lost and not controller._approved_runs)  # pyright: ignore[reportPrivateUsage]
        await controller.dispatch("vibebot/deliver", deliver)
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "ok" and done["content"] == "noted"


@pytest.mark.asyncio
async def test_a_call_held_longer_than_a_week_is_let_go(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    first.hold_tool(BOT, "cb-old", {"server": "s", "tool": "t", "arguments": {}})
    later = time.time() + 8 * 24 * 3600
    monkeypatch.setattr(time, "time", lambda: later)

    reopened = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")

    assert reopened.held_tool(BOT, "cb-old") is None


@pytest.mark.asyncio
async def test_a_call_held_longer_than_a_week_is_let_go_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Felix on #81890: an app server running for over a week must not keep an
    old hold answerable either.
    """
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    sessions.hold_tool(
        BOT, "cb-old", {"server": "s", "tool": "t", "arguments": {}, "argsHash": "h"}
    )
    assert sessions.held_tool(BOT, "cb-old") is not None
    later = time.time() + 8 * 24 * 3600
    monkeypatch.setattr(time, "time", lambda: later)

    assert sessions.held_tool(BOT, "cb-old") is None
    sessions.update_tool(BOT, "cb-old", running=True)
    assert sessions.held_tool(BOT, "cb-old") is None


@pytest.mark.asyncio
async def test_a_run_that_crosses_the_week_keeps_its_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A late answer near the week: its outcome must survive a lost report, or a
    retry would say it did not run.
    """
    sessions = VibeBotLocalSessions(client_id_file=tmp_path / "vibe_bot_client_id")
    start = time.time()
    sessions.hold_tool(
        BOT, "cb-1", {"server": "s", "tool": "t", "arguments": {}, "argsHash": "h"}
    )
    week = 7 * 24 * 3600
    monkeypatch.setattr(time, "time", lambda: start + week - 1)
    sessions.update_tool(BOT, "cb-1", running=True)
    monkeypatch.setattr(time, "time", lambda: start + week + 1)
    sessions.update_tool(BOT, "cb-1", outcome={"outcome": "ok", "content": "done"})

    held = sessions.held_tool(BOT, "cb-1")

    assert held is not None and held["outcome"] == {"outcome": "ok", "content": "done"}


@pytest.mark.asyncio
async def test_a_deny_for_a_call_no_longer_held_is_still_a_refusal(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: the bot hears the user's no, not that the machine forgot."""
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    client = _ClaimingClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, local_tools=_tools(config, _FakeMCP({}))
    )
    try:
        await _follow(controller, session_ids=[])
        await controller.dispatch(
            "vibebot/deliver", {"botWorkflowId": BOT, "deliveries": [_approval("deny")]}
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert [r["outcome"] for r in client.reports if r["kind"] == "tool_result"] == [
        "rejected"
    ]


class _SlowPendingClient(_ClaimingClient):
    """The workflow takes the first `tool_pending`, but its answer is lost."""

    def __init__(self) -> None:
        super().__init__()
        self.lost = False

    async def report(self, bot: str, event: dict[str, Any]) -> bool:
        taken = await super().report(bot, event)
        if event["kind"] == "tool_pending" and not self.lost:
            self.lost = True
            raise TimeoutError("the answer did not come back")
        return taken


@pytest.mark.asyncio
async def test_a_pending_report_whose_answer_was_lost_keeps_the_call_for_the_approval(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted")
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _SlowPendingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    try:
        await _follow(controller)
        await _until(lambda: bool(api.answers))
        await controller.dispatch(
            "vibebot/deliver",
            {"botWorkflowId": BOT, "deliveries": [_approval("allow")]},
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "ok"


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


class _FailingToRun(_FakeMCP):
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
async def test_an_approved_call_that_fails_tells_the_bot_nothing_about_this_machine(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: the approved path keeps host details local, as the direct one does."""
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FailingToRun({"grafana": (ECHO, WRITE)})
    client = await _approve(sessions, config, mcp, _approval("allow"))

    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert "/Users/me" not in json.dumps(done)
    assert done["outcome"] == "failed"
    assert done["error"].startswith("local_tool_failed: ")


def test_the_approval_text_escapes_bidi_and_zero_width_characters_as_the_inbox_does() -> (
    None
):
    """Nayef on #81890: the length check counts the string the user sees."""
    text = approval_text({
        "path": "/tmp/\u202egnp.exe",
        "note": "a\u200bb",
        "city": "Zürich",
    })

    assert text == (
        '{"city": "Zürich", "note": "a\\u200bb", "path": "/tmp/\\u202egnp.exe"}'
    )


def test_the_approval_text_escapes_blank_looking_characters_and_variation_selectors() -> (
    None
):
    """As the workflow's card text, so the 2000-character check measures the same string."""
    text = approval_text({"text": "a\u3164b\ufe0f\U000e0100\ue000"})

    assert text == '{"text": "a\\u3164b\\ufe0f\\udb40\\udd00\\ue000"}'


@pytest.mark.asyncio
async def test_a_retried_approval_request_keeps_the_outcome_of_a_run_that_happened(
    sessions: VibeBotLocalSessions,
) -> None:
    """Bugbot on #81890: a retry of the same call must not reset its hold, or a lost report reruns the tool."""
    call = {
        "server": "grafana",
        "tool": "write_note",
        "arguments": {"text": "x"},
        "argsHash": "h",
    }
    sessions.hold_tool(BOT, "cb-1", call)
    sessions.update_tool(BOT, "cb-1", running=True)
    sessions.update_tool(BOT, "cb-1", outcome={"outcome": "ok", "content": "noted"})

    sessions.hold_tool(BOT, "cb-1", call)

    held = sessions.held_tool(BOT, "cb-1")
    assert held is not None
    assert held["running"] is True
    assert held["outcome"] == {"outcome": "ok", "content": "noted"}


@pytest.mark.asyncio
async def test_an_approval_retried_after_a_lost_report_runs_the_tool_once(
    sessions: VibeBotLocalSessions,
) -> None:
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)}, answer="noted")
    await _approve(sessions, config, mcp, _approval("allow"))
    sessions.hold_tool(
        BOT,
        "cb-1",
        {
            "server": "grafana",
            "tool": "write_note",
            "arguments": {"text": "x"},
            "argsHash": args_hash({"text": "x"}),
        },
    )
    sessions.update_tool(BOT, "cb-1", running=True)

    sessions.hold_tool(
        BOT,
        "cb-1",
        {
            "server": "grafana",
            "tool": "write_note",
            "arguments": {"text": "x"},
            "argsHash": args_hash({"text": "x"}),
        },
    )

    assert (sessions.held_tool(BOT, "cb-1") or {}).get("running") is True
    assert len(mcp.calls) == 1


def test_the_pending_tool_calls_file_is_readable_only_by_its_owner(
    sessions: VibeBotLocalSessions, tmp_path: Path
) -> None:
    """Gabriel on #81890: it holds the bot's tool arguments, like a token file."""
    sessions.hold_tool(
        BOT,
        "cb-1",
        {"server": "s", "tool": "t", "arguments": {"q": "x"}, "argsHash": "h"},
    )

    mode = (tmp_path / "vibe_bot_pending_tools.json").stat().st_mode & 0o777
    assert mode == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("running", "says"), [(False, "did not run"), (True, "may have run")]
)
async def test_an_expired_report_tells_the_bot_whether_the_call_may_have_run(
    sessions: VibeBotLocalSessions, running: bool, says: str
) -> None:
    """Reviewer on #81890: the bot must not retry a write that may already have run."""
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _FakeMCP({"grafana": (ECHO, WRITE)})
    if running:
        sessions.hold_tool(
            BOT,
            "cb-1",
            {
                "server": "grafana",
                "tool": "write_note",
                "arguments": {"text": "x"},
                "argsHash": args_hash({"text": "x"}),
            },
        )
        sessions.update_tool(BOT, "cb-1", running=True)
    client = _ClaimingClient()
    controller = _controller(
        _FakeSessionApi([]), client, sessions, local_tools=_tools(config, mcp)
    )
    try:
        await _follow(controller, session_ids=[])
        await controller.dispatch(
            "vibebot/deliver",
            {"botWorkflowId": BOT, "deliveries": [_approval("allow")]},
        )
        await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    finally:
        await asyncio.gather(*controller.close(), return_exceptions=True)

    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "expired"
    assert says in done.get("error", "")
    assert mcp.calls == []


class _ClosingMCP(_FakeMCP):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
@pytest.mark.parametrize("final", [False, True])
async def test_an_approved_run_outlives_the_connection_that_got_it(
    sessions: VibeBotLocalSessions, final: bool
) -> None:
    """Bugbot on #81890: a window reload or a closed connection must not kill a
    write the user allowed; its servers close once it is done.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _ClosingMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.3)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=_tools(config, mcp))
    await _follow(controller)
    await _until(lambda: bool(api.answers))
    await controller.dispatch(
        "vibebot/deliver", {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    )
    await asyncio.sleep(0.05)

    await asyncio.gather(*controller.close(), return_exceptions=True)
    closing = asyncio.create_task(
        controller.aclose_tools() if final else controller.drop_tools()
    )
    await asyncio.sleep(0.05)

    assert mcp.closed == 0
    await closing
    await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    await _until(lambda: mcp.closed == 1)
    [done] = [r for r in client.reports if r["kind"] == "tool_result"]
    assert done["outcome"] == "ok"
    assert mcp.calls == [("grafana", "write_note", {"text": "x"})]


@pytest.mark.asyncio
async def test_a_detach_mid_run_never_closes_the_servers_of_the_next_connection(
    sessions: VibeBotLocalSessions,
) -> None:
    """Reviewer on #81890: the run's servers close after it; the next
    connection's own servers stay open.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    first = _ClosingMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.3)
    second = _ClosingMCP({"grafana": (ECHO, WRITE)}, answer="noted")
    runtimes = iter((first, second))

    async def read_config() -> VibeConfigSchema:
        return config

    async def make_mcp() -> LocalMCP:
        return next(runtimes)

    tools = LocalTools(read_config=read_config, mcp=make_mcp)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    client = _ClaimingClient()
    controller = _controller(api, client, sessions, local_tools=tools)
    await _follow(controller)
    await _until(lambda: bool(api.answers))
    await controller.dispatch(
        "vibebot/deliver", {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    )
    await asyncio.sleep(0.05)

    await asyncio.gather(*controller.close(), return_exceptions=True)
    await controller.drop_tools()
    await tools.list({}, "desk")
    await _until(lambda: any(r["kind"] == "tool_result" for r in client.reports))
    await _until(lambda: first.closed == 1)
    await asyncio.sleep(0.05)

    assert second.closed == 0
    await asyncio.gather(*controller.close(), return_exceptions=True)
    await controller.aclose_tools()
    assert second.closed == 1


@pytest.mark.asyncio
async def test_the_final_close_after_a_detach_mid_run_closes_the_detached_servers(
    sessions: VibeBotLocalSessions,
) -> None:
    """Reviewer on #81890: quitting right after a reload must not leave the
    detached servers running.
    """
    config = _config(vibe_bot_local_tools=[{"server": "grafana", "approval": "ask"}])
    mcp = _ClosingMCP({"grafana": (ECHO, WRITE)}, answer="noted", delay=0.3)
    api = _FakeSessionApi([
        _call(
            "cb-1",
            "call_local_tool",
            {"server": "grafana", "tool": "write_note", "arguments": {"text": "x"}},
        )
    ])
    controller = _controller(
        api, _ClaimingClient(), sessions, local_tools=_tools(config, mcp)
    )
    await _follow(controller)
    await _until(lambda: bool(api.answers))
    await controller.dispatch(
        "vibebot/deliver", {"botWorkflowId": BOT, "deliveries": [_approval("allow")]}
    )
    await asyncio.sleep(0.05)
    await asyncio.gather(*controller.close(), return_exceptions=True)
    await controller.drop_tools()

    await controller.aclose_tools()

    assert mcp.closed == 1


@pytest.mark.asyncio
async def test_the_final_close_gives_up_on_a_run_that_ignores_its_cancel(
    sessions: VibeBotLocalSessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bugbot on #81890: a run stuck past its cancel must not hang the shutdown."""
    from vibe.app_server import _vibe_bot

    monkeypatch.setattr(_vibe_bot, "MAX_CALL_SECONDS", 0.05)
    monkeypatch.setattr(_vibe_bot, "_RUN_GRACE_SECONDS", 0.05)
    release = asyncio.Event()

    async def stubborn() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    controller = _controller(_FakeSessionApi([]), _ClaimingClient(), sessions)
    stuck = asyncio.create_task(stubborn())
    controller._approved_runs[BOT, "cb-1"] = stuck  # pyright: ignore[reportPrivateUsage]
    try:
        await asyncio.wait_for(controller.aclose_tools(), 1)
    finally:
        release.set()
        await stuck
