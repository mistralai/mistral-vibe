from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import JsonValue
import pytest

from mistralai_vibe_local_harness.protocol import (
    RustProvidedToolCall,
    RustProvidedToolCallAction,
    RustToolFailedEvent,
    RustToolSucceededEvent,
)
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import _session_peers
from vibe.app_server._provided_tools import (
    VIBE_PROVIDED_TOOL_NAMES,
    VIBE_TOOL_GROUP,
    VibeProvidedTools,
    vibe_tool_groups,
)
from vibe.app_server._session_peers import (
    ARCHIVE_SESSIONS_TOOL_NAME,
    LIST_SESSIONS_TOOL_NAME,
    READ_SESSION_TOOL_NAME,
    SEND_TO_SESSION_TOOL_NAME,
    SessionPeerError,
)
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.app_server.protocol import (
    PeerSessionArchived,
    PeerSessionMessage,
    PeerSessionSummary,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerSendParams,
    SessionPeerSendResponse,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.tools.base import ToolPermission
from vibe.core.tools.manager import ToolManager
from vibe.core.tools.permissions import PermissionStore

_PEER_TOOLS = [
    LIST_SESSIONS_TOOL_NAME,
    READ_SESSION_TOOL_NAME,
    SEND_TO_SESSION_TOOL_NAME,
    ARCHIVE_SESSIONS_TOOL_NAME,
]


class _FakePeers:
    def __init__(self) -> None:
        self.lists: list[SessionPeerListParams] = []
        self.reads: list[SessionPeerReadParams] = []
        self.sends: list[SessionPeerSendParams] = []
        self.archives: list[SessionPeerArchiveParams] = []
        self.hang = False

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse:
        self.lists.append(params)
        return SessionPeerListResponse(
            sessions=[
                PeerSessionSummary(
                    session_id="other", title="Flaky test", status="idle"
                )
            ]
        )

    async def read_peer_session(
        self, params: SessionPeerReadParams
    ) -> SessionPeerReadResponse:
        self.reads.append(params)
        return SessionPeerReadResponse(
            session_id=params.target_session_id,
            status="idle",
            messages=[PeerSessionMessage(role="assistant", text="Fixed it.")],
        )

    async def send_to_peer_session(
        self, params: SessionPeerSendParams
    ) -> SessionPeerSendResponse:
        self.sends.append(params)
        if self.hang:
            await asyncio.Event().wait()
        return SessionPeerSendResponse(
            session_id=params.target_session_id, queued=False
        )

    async def archive_peer_sessions(
        self, params: SessionPeerArchiveParams
    ) -> SessionPeerArchiveResponse:
        self.archives.append(params)
        return SessionPeerArchiveResponse(
            archived=[
                PeerSessionArchived(session_id=session_id)
                for session_id in params.session_ids
            ]
        )


def _executor(
    tmp_path: Path,
    peers: _FakePeers,
    *,
    awaits_user: bool = False,
    session_id: str = "me",
):
    return VibeProvidedTools().executor_factory(
        tmp_path,
        max_todos=lambda: 10,
        session_peers=peers,
        awaits_user=lambda _session_id: awaits_user,
    )(session_id)


def _call(
    action_id: str, tool_name: str, **arguments: JsonValue
) -> RustProvidedToolCallAction:
    return RustProvidedToolCallAction(
        action_id=action_id,
        turn_id="turn-1",
        call_id=f"call-{action_id}",
        call=RustProvidedToolCall(
            group_name=VIBE_TOOL_GROUP, tool_name=tool_name, arguments=arguments
        ),
    )


def _failure(event: object) -> str:
    assert isinstance(event, RustToolFailedEvent)
    return event.result.error.message


def test_the_peer_tools_are_offered_only_to_a_client_that_hosts_peers() -> None:
    def names(groups: list) -> list[str]:
        return [tool.name for group in groups for tool in group.tools]

    assert not set(_PEER_TOOLS) & set(names(vibe_tool_groups({"bash"})))
    assert set(_PEER_TOOLS) <= set(
        names(vibe_tool_groups({"bash"}, session_peers=True))
    )
    filtered = vibe_tool_groups(
        {"bash"}, session_peers=True, disabled_tools=[SEND_TO_SESSION_TOOL_NAME]
    )
    assert SEND_TO_SESSION_TOOL_NAME not in names(filtered)
    assert READ_SESSION_TOOL_NAME in names(filtered)


@pytest.mark.asyncio
async def test_list_sessions_returns_what_the_client_lists(tmp_path: Path) -> None:
    """The client knows which listed session is the caller and leaves it out."""
    peers = _FakePeers()

    listed = await _executor(tmp_path, peers)(_call("list", LIST_SESSIONS_TOOL_NAME))

    assert isinstance(listed, RustToolSucceededEvent)
    assert peers.lists == [SessionPeerListParams(limit=20)]
    assert SessionPeerListResponse.model_validate(
        listed.result.structured_content
    ).sessions == [
        PeerSessionSummary(session_id="other", title="Flaky test", status="idle")
    ]


@pytest.mark.asyncio
async def test_list_sessions_passes_its_filters_and_page(tmp_path: Path) -> None:
    peers = _FakePeers()

    listed = await _executor(tmp_path, peers)(
        _call(
            "list",
            LIST_SESSIONS_TOOL_NAME,
            session_ids=["other"],
            statuses=["working", "waiting_for_user"],
            limit=5,
            cursor="page-2",
        )
    )

    assert isinstance(listed, RustToolSucceededEvent)
    assert peers.lists == [
        SessionPeerListParams(
            session_ids=["other"],
            statuses=["working", "waiting_for_user"],
            limit=5,
            cursor="page-2",
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments", [{"limit": 51}, {"statuses": ["busy"]}, {"session_ids": []}]
)
async def test_list_sessions_refuses_arguments_out_of_bounds(
    tmp_path: Path, arguments: dict[str, JsonValue]
) -> None:
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers)(
        _call("list", LIST_SESSIONS_TOOL_NAME, **arguments)
    )

    assert isinstance(refused, RustToolFailedEvent)
    assert peers.lists == []


@pytest.mark.asyncio
async def test_read_session_pages_back_through_the_client(tmp_path: Path) -> None:
    peers = _FakePeers()

    read = await _executor(tmp_path, peers)(
        _call(
            "read",
            READ_SESSION_TOOL_NAME,
            session_id="other",
            cursor="older",
            include_tool_calls=True,
        )
    )

    assert isinstance(read, RustToolSucceededEvent)
    assert peers.reads == [
        SessionPeerReadParams(
            target_session_id="other",
            limit=10,
            cursor="older",
            include_tool_calls=True,
            max_message_chars=4000,
        )
    ]


@pytest.mark.asyncio
async def test_read_session_never_waits(tmp_path: Path) -> None:
    refused = await _executor(tmp_path, _FakePeers())(
        _call("read", READ_SESSION_TOOL_NAME, session_id="other", wait_seconds=60)
    )

    assert isinstance(refused, RustToolFailedEvent)


def test_list_and_read_can_run_from_a_program_but_acting_cannot() -> None:
    exposures = {
        tool.name: tool.exposure
        for group in vibe_tool_groups({"bash"}, session_peers=True)
        for tool in group.tools
    }

    assert exposures[LIST_SESSIONS_TOOL_NAME] == "direct_and_programmatic"
    assert exposures[READ_SESSION_TOOL_NAME] == "direct_and_programmatic"
    assert exposures[SEND_TO_SESSION_TOOL_NAME] == "direct"
    assert exposures[ARCHIVE_SESSIONS_TOOL_NAME] == "direct"


@pytest.mark.asyncio
async def test_archive_sessions_asks_the_client_once_for_every_session(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()

    archived = await _executor(tmp_path, peers)(
        _call("archive", ARCHIVE_SESSIONS_TOOL_NAME, session_ids=["a", "b", "a"])
    )

    assert isinstance(archived, RustToolSucceededEvent)
    assert peers.archives == [SessionPeerArchiveParams(session_ids=["a", "b"])]


def test_a_listed_session_leaves_out_what_says_nothing() -> None:
    assert PeerSessionSummary(
        session_id="other", status="idle", queued_messages=0
    ).model_dump(mode="json", by_alias=False) == {
        "session_id": "other",
        "status": "idle",
    }


@pytest.mark.asyncio
async def test_send_to_session_keys_the_message_by_the_tool_call(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()
    execute = _executor(tmp_path, peers)

    sent = await execute(
        _call("send", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Rebase")
    )
    await execute(
        _call("send", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Rebase")
    )
    await execute(
        _call("again", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Rebase")
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [send.text for send in peers.sends] == ["Rebase"] * 3
    keys = [send.idempotency_key for send in peers.sends]
    assert keys[0] == keys[1] != keys[2]


@pytest.mark.asyncio
async def test_a_session_another_one_wrote_to_cannot_message_sessions(
    tmp_path: Path,
) -> None:
    """Otherwise two sessions could keep messaging each other with nobody
    watching.
    """
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers, awaits_user=True)(
        _call("send", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Hi")
    )

    assert "until the user writes to it" in _failure(refused)
    assert peers.sends == []


@pytest.mark.asyncio
async def test_a_session_another_one_wrote_to_cannot_archive_sessions(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers, awaits_user=True)(
        _call("archive", ARCHIVE_SESSIONS_TOOL_NAME, session_ids=["parent"])
    )

    assert "until the user writes to it" in _failure(refused)
    assert peers.archives == []


@pytest.mark.asyncio
async def test_an_unconfirmed_message_is_reported_as_maybe_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_session_peers, "PEER_REQUEST_TIMEOUT_SECONDS", 0.01)
    peers = _FakePeers()
    peers.hang = True

    unknown = await _executor(tmp_path, peers)(
        _call("send", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Hi")
    )

    assert "may or may not have been sent" in _failure(unknown)


@pytest.mark.asyncio
async def test_a_client_refusal_reaches_the_model(tmp_path: Path) -> None:
    class Refusing(_FakePeers):
        async def read_peer_session(
            self, params: SessionPeerReadParams
        ) -> SessionPeerReadResponse:
            raise SessionPeerError("No session other in this project")

    refused = await _executor(tmp_path, Refusing())(
        _call("read", READ_SESSION_TOOL_NAME, session_id="other")
    )

    assert _failure(refused) == "No session other in this project"


@pytest.mark.asyncio
async def test_peer_tools_are_unavailable_without_a_client_that_hosts_peers(
    tmp_path: Path,
) -> None:
    execute = VibeProvidedTools().executor_factory(tmp_path, max_todos=lambda: 10)("me")

    refused = await execute(_call("list", LIST_SESSIONS_TOOL_NAME))

    assert "not reachable" in _failure(refused)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "decision"),
    [
        (LIST_SESSIONS_TOOL_NAME, "allow"),
        (READ_SESSION_TOOL_NAME, "allow"),
        (SEND_TO_SESSION_TOOL_NAME, "ask"),
        (ARCHIVE_SESSIONS_TOOL_NAME, "ask"),
    ],
)
async def test_only_what_acts_on_sessions_asks_by_default(
    tmp_path: Path, tool_name: str, decision: str
) -> None:
    orchestrator = FakeConfigOrchestrator(build_test_vibe_config())
    store = PermissionStore()
    manager = ToolManager(
        lambda: orchestrator.config,
        defer_mcp=True,
        cwd=tmp_path,
        harness_files=HarnessFilesManager().for_session(tmp_path),
        permission_getter=store.get_tool_permission,
    )
    resolver = UnifiedPermissionResolver(manager, store, orchestrator)
    resolver.provided_names.register(VIBE_TOOL_GROUP, VIBE_PROVIDED_TOOL_NAMES)

    outcome = await resolver.resolve(f"{VIBE_TOOL_GROUP}.{tool_name}", {})
    assert outcome.decision == decision

    store.set_tool_permission(tool_name, ToolPermission.NEVER)
    outcome = await resolver.resolve(f"{VIBE_TOOL_GROUP}.{tool_name}", {})
    assert outcome.decision == "deny"
