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
    MARK_SESSIONS_SEEN_TOOL_NAME,
    READ_SESSION_TOOL_NAME,
    RESTORE_SESSIONS_TOOL_NAME,
    SEND_TO_SESSION_TOOL_NAME,
    SessionPeerError,
    TurnStarts,
)
from vibe.app_server._session_spawn import MAX_SESSION_HOPS, MAX_TURN_STARTED_SESSIONS
from vibe.app_server._unified_permissions import UnifiedPermissionResolver
from vibe.app_server.protocol import (
    ClientCapabilities,
    PeerSessionArchived,
    PeerSessionMarkedSeen,
    PeerSessionMessage,
    PeerSessionRestored,
    PeerSessionSummary,
    SessionMessageSender,
    SessionPeerArchiveParams,
    SessionPeerArchiveResponse,
    SessionPeerListParams,
    SessionPeerListResponse,
    SessionPeerMarkSeenParams,
    SessionPeerMarkSeenResponse,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerRestoreParams,
    SessionPeerRestoreResponse,
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
    RESTORE_SESSIONS_TOOL_NAME,
    MARK_SESSIONS_SEEN_TOOL_NAME,
]


_PARENT_ID = "c07d9946-6298-0d14-2abf-daa76a0a0a3f"


class _FakePeers:
    """Lists ``other`` to the model's own `list_sessions`; resolving a
    reference reads every listed session, archived included.
    """

    def __init__(self, sessions: list[PeerSessionSummary] | None = None) -> None:
        self.sessions = sessions or [
            PeerSessionSummary(session_id="other", title="Flaky test", status="idle"),
            PeerSessionSummary(session_id="a", status="idle"),
            PeerSessionSummary(session_id="b", status="idle"),
            PeerSessionSummary(session_id="parent", status="working"),
        ]
        # Real sessions a paged project listing does not reach.
        self.unlisted: list[PeerSessionSummary] = []
        self.resolves = 0
        self.lists: list[SessionPeerListParams] = []
        self.reads: list[SessionPeerReadParams] = []
        self.sends: list[SessionPeerSendParams] = []
        self.archives: list[SessionPeerArchiveParams] = []
        self.restores: list[SessionPeerRestoreParams] = []
        self.marks: list[SessionPeerMarkSeenParams] = []
        self.hang = False

    def client_capabilities(self) -> ClientCapabilities:
        return ClientCapabilities(
            session_peers=True, session_peer_restore=True, session_peer_mark_seen=True
        )

    async def list_peer_sessions(
        self, params: SessionPeerListParams
    ) -> SessionPeerListResponse:
        if params.statuses is not None and "archived" in params.statuses:
            self.resolves += 1
            if params.session_ids is not None:
                return SessionPeerListResponse(
                    sessions=[
                        session
                        for session in [*self.sessions, *self.unlisted]
                        if session.session_id in params.session_ids
                    ]
                )
            return SessionPeerListResponse(sessions=self.sessions)
        self.lists.append(params)
        return SessionPeerListResponse(sessions=self.sessions[:1])

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

    async def restore_peer_sessions(
        self, params: SessionPeerRestoreParams
    ) -> SessionPeerRestoreResponse:
        self.restores.append(params)
        return SessionPeerRestoreResponse(
            restored=[
                PeerSessionRestored(session_id=session_id)
                for session_id in params.session_ids
            ]
        )

    async def mark_peer_sessions_seen(
        self, params: SessionPeerMarkSeenParams
    ) -> SessionPeerMarkSeenResponse:
        self.marks.append(params)
        return SessionPeerMarkSeenResponse(
            marked=[
                PeerSessionMarkedSeen(session_id=session_id)
                for session_id in params.session_ids
            ]
        )


def _executor(
    tmp_path: Path,
    peers: _FakePeers,
    *,
    hops: int = 0,
    started_by: str | None = None,
    session_id: str = "me",
    turn_starts: list[int] | None = None,
):
    async def sender_of(caller: str) -> SessionMessageSender:
        return SessionMessageSender(session_id=caller, title="Fix CI")

    starts = turn_starts if turn_starts is not None else [0]

    def count_turn_start(_session_id: str, delta: int) -> None:
        starts[0] += delta

    return VibeProvidedTools().executor_factory(
        tmp_path,
        max_todos=lambda: 10,
        session_peers=peers,
        hops_of=lambda _session_id: hops,
        sender_of=sender_of,
        starter_of=lambda _session_id: started_by,
        turn_starts=TurnStarts(
            of=lambda _session_id: starts[0], count=count_turn_start
        ),
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
        names(
            vibe_tool_groups(
                {"bash"},
                session_peers=True,
                session_peer_restore=True,
                session_peer_mark_seen=True,
            )
        )
    )
    basic = names(vibe_tool_groups({"bash"}, session_peers=True))
    assert RESTORE_SESSIONS_TOOL_NAME not in basic
    assert MARK_SESSIONS_SEEN_TOOL_NAME not in basic
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
        for group in vibe_tool_groups(
            {"bash"},
            session_peers=True,
            session_peer_restore=True,
            session_peer_mark_seen=True,
        )
        for tool in group.tools
    }

    assert exposures[LIST_SESSIONS_TOOL_NAME] == "direct_and_programmatic"
    assert exposures[READ_SESSION_TOOL_NAME] == "direct_and_programmatic"
    assert exposures[SEND_TO_SESSION_TOOL_NAME] == "direct"
    assert exposures[ARCHIVE_SESSIONS_TOOL_NAME] == "direct"
    assert exposures[RESTORE_SESSIONS_TOOL_NAME] == "direct"
    assert exposures[MARK_SESSIONS_SEEN_TOOL_NAME] == "direct"


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


@pytest.mark.asyncio
async def test_restore_sessions_asks_the_client_once_for_every_session(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()

    restored = await _executor(tmp_path, peers)(
        _call("restore", RESTORE_SESSIONS_TOOL_NAME, session_ids=["a", "b", "a"])
    )

    assert isinstance(restored, RustToolSucceededEvent)
    assert peers.restores == [SessionPeerRestoreParams(session_ids=["a", "b"])]


@pytest.mark.asyncio
async def test_mark_sessions_seen_asks_the_client_once_for_every_session(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()

    marked = await _executor(tmp_path, peers)(
        _call("mark", MARK_SESSIONS_SEEN_TOOL_NAME, session_ids=["a", "b", "a"])
    )

    assert isinstance(marked, RustToolSucceededEvent)
    assert peers.marks == [SessionPeerMarkSeenParams(session_ids=["a", "b"])]


def test_a_listed_session_says_it_is_unseen_only_when_it_is() -> None:
    assert PeerSessionSummary(
        session_id="other", status="idle", is_unseen=True
    ).model_dump(mode="json", by_alias=False) == {
        "session_id": "other",
        "status": "idle",
        "is_unseen": True,
    }


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
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Rebase",
            delivery="start_turn",
        )
    )
    await execute(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Rebase",
            delivery="start_turn",
        )
    )
    await execute(
        _call(
            "again",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Rebase",
            delivery="start_turn",
        )
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [send.text for send in peers.sends] == ["Rebase"] * 3
    keys = [send.idempotency_key for send in peers.sends]
    assert keys[0] == keys[1] != keys[2]


@pytest.mark.asyncio
async def test_a_message_names_the_session_that_sends_it(tmp_path: Path) -> None:
    """The client only passes the sender on; it never works it out."""
    peers = _FakePeers()

    await _executor(tmp_path, peers)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Hi",
            delivery="start_turn",
        )
    )

    assert [send.sender for send in peers.sends] == [
        SessionMessageSender(session_id="me", title="Fix CI")
    ]


@pytest.mark.asyncio
async def test_a_message_counts_one_hop_more_than_the_turn_sending_it(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session in a turn another session's message started.
    *Do*: Message another session, for its next turn only.
    *Assert*: The client gets the delivery as asked and one hop more, so the
    receiving session knows how long the chain is.
    """
    peers = _FakePeers()

    sent = await _executor(tmp_path, peers, hops=3)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="FYI: CI is green",
            delivery="queue",
        )
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [(send.delivery, send.hops) for send in peers.sends] == [("queue", 4)]


@pytest.mark.asyncio
async def test_a_started_session_reports_back_without_naming_its_starter(
    tmp_path: Path,
) -> None:
    """The app server fills in the starter it saved, so a model that would
    mistype a long id never has to copy it.
    """
    peers = _FakePeers()

    sent = await _executor(
        tmp_path, peers, started_by="c07d9946-6298-0d14-2abf-daa76a0a0a3f"
    )(
        _call(
            "report",
            SEND_TO_SESSION_TOOL_NAME,
            message="Found it",
            delivery="start_turn",
        )
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [send.target_session_id for send in peers.sends] == [
        "c07d9946-6298-0d14-2abf-daa76a0a0a3f"
    ]


@pytest.mark.asyncio
async def test_a_session_nobody_started_must_name_the_session_to_message(
    tmp_path: Path,
) -> None:
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers)(
        _call("send", SEND_TO_SESSION_TOOL_NAME, message="Hi", delivery="queue")
    )

    assert "name the session to message" in _failure(refused)
    assert peers.sends == []


_PROJECT = [
    PeerSessionSummary(session_id=_PARENT_ID, title="Find articles", status="working"),
    PeerSessionSummary(
        session_id="c13e64cd-2920-5c50-c71d-63ab4763b090",
        title="Funny article finder 2",
        status="working",
    ),
    PeerSessionSummary(
        session_id="c13e64cd-0000-4000-8000-000000000000",
        title="Funny article finder 3",
        status="idle",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reference", [_PARENT_ID, "c07d9946", "Find articles", " find ARTICLES "]
)
async def test_a_session_is_named_by_its_id_a_short_id_or_its_title(
    tmp_path: Path, reference: str
) -> None:
    peers = _FakePeers(_PROJECT)

    sent = await _executor(tmp_path, peers)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id=reference,
            message="Done",
            delivery="start_turn",
        )
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [send.target_session_id for send in peers.sends] == [_PARENT_ID]


@pytest.mark.asyncio
async def test_list_sessions_resolves_short_ids_and_titles(tmp_path: Path) -> None:
    """*Prepare*: A project of sessions.
    *Do*: List them by a short id, a title, a name matching none and the
    caller itself.
    *Assert*: The client is asked for the full ids; the unknown name is listed
    as not found, as an unknown id is, and the caller is left out.
    """
    peers = _FakePeers(_PROJECT)
    run = _executor(tmp_path, peers)

    listed = await run(
        _call(
            "list",
            LIST_SESSIONS_TOOL_NAME,
            session_ids=["c07d9946", "Funny article finder 2", "Funny finder", "me"],
        )
    )
    only_self = await run(_call("only", LIST_SESSIONS_TOOL_NAME, session_ids=["me"]))

    assert isinstance(listed, RustToolSucceededEvent)
    assert isinstance(only_self, RustToolSucceededEvent)
    assert [params.session_ids for params in peers.lists] == [
        [_PARENT_ID, "c13e64cd-2920-5c50-c71d-63ab4763b090"]
    ]
    response = SessionPeerListResponse.model_validate(listed.result.structured_content)
    assert response.not_found == ["Funny finder"]


@pytest.mark.asyncio
async def test_a_full_id_reaches_a_session_the_project_listing_does_not(
    tmp_path: Path,
) -> None:
    """A project's listing is paged; a full id is looked up as it is, so a
    session past the pages read is still found.
    """
    peers = _FakePeers(_PROJECT)
    far = "0f0f0f0f-0000-4000-8000-00000000beef"
    peers.unlisted = [PeerSessionSummary(session_id=far, status="idle")]

    sent = await _executor(tmp_path, peers)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id=far,
            message="Done",
            delivery="start_turn",
        )
    )

    assert isinstance(sent, RustToolSucceededEvent)
    assert [send.target_session_id for send in peers.sends] == [far]


@pytest.mark.asyncio
async def test_a_mistyped_id_is_answered_with_the_closest_session(
    tmp_path: Path,
) -> None:
    """*Prepare*: A project holding the session a child means to report to.
    *Do*: Message it by its id with two characters dropped, as a model did.
    *Assert*: Nothing is sent, and the error names the session it meant, with
    its full id and title, and says how to call again.
    """
    peers = _FakePeers(_PROJECT)

    refused = await _executor(tmp_path, peers)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id=_PARENT_ID.replace("0a0a", "0a", 1),
            message="Done",
            delivery="start_turn",
        )
    )

    error = _failure(refused)
    assert peers.sends == []
    assert f'The closest: `{_PARENT_ID}` "Find articles" (working).' in error
    assert "Call again with one of those ids" in error


@pytest.mark.asyncio
async def test_a_short_id_that_names_two_sessions_lists_both(tmp_path: Path) -> None:
    peers = _FakePeers(_PROJECT)

    refused = await _executor(tmp_path, peers)(
        _call("read", READ_SESSION_TOOL_NAME, session_id="c13e64cd")
    )

    error = _failure(refused)
    assert peers.reads == []
    assert "`c13e64cd` names 2 sessions" in error
    assert "Funny article finder 2" in error
    assert "Funny article finder 3" in error


@pytest.mark.asyncio
async def test_a_name_that_matches_nothing_points_to_list_sessions(
    tmp_path: Path,
) -> None:
    peers = _FakePeers(_PROJECT)

    refused = await _executor(tmp_path, peers)(
        _call("read", READ_SESSION_TOOL_NAME, session_id="zzzz")
    )

    assert "No session of this project is named `zzzz`" in _failure(refused)
    assert "list_sessions" in _failure(refused)


@pytest.mark.asyncio
async def test_archiving_fails_only_the_sessions_it_cannot_name(tmp_path: Path) -> None:
    peers = _FakePeers(_PROJECT)

    archived = await _executor(tmp_path, peers)(
        _call(
            "archive",
            ARCHIVE_SESSIONS_TOOL_NAME,
            session_ids=["Funny article finder 2", "c07d9946-typo"],
        )
    )

    assert isinstance(archived, RustToolSucceededEvent)
    assert peers.archives == [
        SessionPeerArchiveParams(session_ids=["c13e64cd-2920-5c50-c71d-63ab4763b090"])
    ]
    failed = SessionPeerArchiveResponse.model_validate(
        archived.result.structured_content
    ).failed
    assert [entry.session_id for entry in failed] == ["c07d9946-typo"]
    assert _PARENT_ID in failed[0].error


@pytest.mark.asyncio
async def test_a_session_cannot_message_itself_by_name(tmp_path: Path) -> None:
    peers = _FakePeers(_PROJECT)

    refused = await _executor(tmp_path, peers, session_id=_PARENT_ID)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id=_PARENT_ID,
            message="Hi",
            delivery="queue",
        )
    )

    assert "this session itself" in _failure(refused)
    assert peers.sends == []


@pytest.mark.asyncio
async def test_a_message_must_say_how_it_is_delivered(tmp_path: Path) -> None:
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers)(
        _call("send", SEND_TO_SESSION_TOOL_NAME, session_id="other", message="Hi")
    )

    assert "delivery" in _failure(refused)
    assert peers.sends == []


@pytest.mark.asyncio
async def test_sessions_cannot_message_each_other_past_the_hop_limit(
    tmp_path: Path,
) -> None:
    """*Prepare*: Sessions that messaged each other up to the limit with no
    user message between.
    *Do*: Send one more message, then send one at the hop before.
    *Assert*: The last hop is refused with a reason the client can read and
    nothing reaches the client; the one before goes out.
    """
    peers = _FakePeers()

    refused = await _executor(tmp_path, peers, hops=MAX_SESSION_HOPS)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Your turn",
            delivery="start_turn",
        )
    )
    allowed = await _executor(tmp_path, peers, hops=MAX_SESSION_HOPS - 1)(
        _call(
            "last",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Your turn",
            delivery="start_turn",
        )
    )

    assert isinstance(refused, RustToolFailedEvent)
    assert "Tell the user instead" in refused.result.error.message
    assert refused.result.error.details == {"reason": "session_hops"}
    assert isinstance(allowed, RustToolSucceededEvent)
    assert [send.hops for send in peers.sends] == [MAX_SESSION_HOPS]


@pytest.mark.asyncio
async def test_a_message_that_may_start_a_turn_counts_against_the_turn_cap(
    tmp_path: Path,
) -> None:
    """*Prepare*: A turn one start short of `MAX_TURN_STARTED_SESSIONS`.
    *Do*: Send a message that may start a turn, then another, then a queued
        one.
    *Assert*: The first counts and goes out; the second is refused at the cap
    with a reason the client can read; a queued message starts no turn, so
    it is not counted.
    """
    peers = _FakePeers()
    starts = [MAX_TURN_STARTED_SESSIONS - 1]
    execute = _executor(tmp_path, peers, turn_starts=starts)

    def send(action_id: str, delivery: str) -> RustProvidedToolCallAction:
        return _call(
            action_id,
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Fix it",
            delivery=delivery,
        )

    allowed = await execute(send("first", "start_turn"))
    refused = await execute(send("second", "start_turn"))
    queued = await execute(send("queued", "queue"))

    assert isinstance(allowed, RustToolSucceededEvent)
    assert isinstance(refused, RustToolFailedEvent)
    assert refused.result.error.details == {"reason": "started_sessions"}
    assert isinstance(queued, RustToolSucceededEvent)
    assert [send.delivery for send in peers.sends] == ["start_turn", "queue"]
    assert starts == [MAX_TURN_STARTED_SESSIONS]


@pytest.mark.asyncio
async def test_any_session_can_archive_restore_and_mark_sessions_seen(
    tmp_path: Path,
) -> None:
    """These start nothing, so a long chain of messages does not hold them."""
    peers = _FakePeers()
    execute = _executor(tmp_path, peers, hops=MAX_SESSION_HOPS)

    archived = await execute(
        _call("archive", ARCHIVE_SESSIONS_TOOL_NAME, session_ids=["parent"])
    )
    restored = await execute(
        _call("restore", RESTORE_SESSIONS_TOOL_NAME, session_ids=["parent"])
    )
    marked = await execute(
        _call("mark", MARK_SESSIONS_SEEN_TOOL_NAME, session_ids=["parent"])
    )

    assert isinstance(archived, RustToolSucceededEvent)
    assert isinstance(restored, RustToolSucceededEvent)
    assert isinstance(marked, RustToolSucceededEvent)


@pytest.mark.asyncio
async def test_mark_sessions_seen_takes_short_ids_and_titles(tmp_path: Path) -> None:
    """*Prepare*: A project of sessions.
    *Do*: Mark one by a short id, one by its title, and a name matching none.
    *Assert*: The client gets the full ids; the unknown name fails alone.
    """
    peers = _FakePeers(_PROJECT)

    marked = await _executor(tmp_path, peers)(
        _call(
            "mark",
            MARK_SESSIONS_SEEN_TOOL_NAME,
            session_ids=["c07d9946", "Funny article finder 2", "Funny finder"],
        )
    )

    assert isinstance(marked, RustToolSucceededEvent)
    assert peers.marks == [
        SessionPeerMarkSeenParams(
            session_ids=[_PARENT_ID, "c13e64cd-2920-5c50-c71d-63ab4763b090"]
        )
    ]
    response = SessionPeerMarkSeenResponse.model_validate(
        marked.result.structured_content
    )
    assert [failure.session_id for failure in response.failed] == ["Funny finder"]


@pytest.mark.asyncio
async def test_an_unconfirmed_message_is_reported_as_maybe_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_session_peers, "PEER_REQUEST_TIMEOUT_SECONDS", 0.01)
    peers = _FakePeers()
    peers.hang = True

    unknown = await _executor(tmp_path, peers)(
        _call(
            "send",
            SEND_TO_SESSION_TOOL_NAME,
            session_id="other",
            message="Hi",
            delivery="start_turn",
        )
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
        (RESTORE_SESSIONS_TOOL_NAME, "ask"),
        (MARK_SESSIONS_SEEN_TOOL_NAME, "allow"),
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
