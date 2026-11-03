"""Tests of the app server talking to the local runtime directly.

The Session API switch is off by default, so the app server takes this path
unless ``vibe_cli_local_runtime_session_api_migration`` or
``VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API`` turns the Session API on.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import contextlib
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import pytest

import mistralai_vibe_local_harness.session_protocol as harness_session_protocol
from mistralai_vibe_local_harness.vibe._session import (
    HarnessSessionSubscription,
    _SessionBackendResult,
)
from tests.conftest import build_test_vibe_config
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server._unified_scheduled_loops import ScheduledPrompt
from vibe.app_server.events import (
    ChildSessionUpdated,
    HistoryEntryAdded,
    HistoryEntryUpdated,
    ServerWarning,
    SessionSnapshot,
    TurnRetrying,
)
from vibe.app_server.models import (
    CompletedEffectState,
    IdleSessionStatus,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicMessageEntry,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionState,
    PublicTurnStatus,
    ShellEffectDetail,
    SkillEffectDetail,
    SkillEffectInput,
    TextContentBlock,
)
from vibe.app_server.protocol import (
    CallbackResultError,
    MessageAnnotations,
    PageRequest,
    ProtocolErrorCode,
    SessionCompactParams,
    SessionReadParams,
    SessionResumeParams,
    SessionStartParams,
    SessionTextContentBlock,
    TurnContextInputEntry,
    TurnEnqueueParams,
    TurnEnqueueResponse,
    TurnQueueEditAbandonParams,
    TurnQueueReadParams,
    TurnQueueReplaceParams,
    TurnQueueReplaceResponse,
    TurnStartParams,
    TurnUserInputEntry,
)
from vibe.core.config import SessionLoggingConfig
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.telemetry.send import TelemetryClient
from vibe.core.tools.builtins.skill import already_loaded_message
from vibe.permissions import PathGrantScope
from vibe.user_content import UserDisplayContent

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.session_protocol import JsonObject
    from mistralai_vibe_local_harness.vibe._storage import SessionPin
from tests.app_server.test_unified_harness_backend_adapter import (
    _approval_action,
    _compaction_checkpoint,
    _harness_backend_host,
    _inert_adapter,
    _priced_runtime,
    _RecordingSession,
    _session_stub,
    _skill_adapter,
    _stats_adapter,
    _unified_adapter_with_real_context,
    _usage_state,
    _write_workspace_skill,
)


class _RecordingPermissions:
    """Stands in for the resolver so the assertion is about what the adapter records."""

    def __init__(self) -> None:
        self.grants: list[tuple[str, tuple[str, ...], bool]] = []

    async def grant(
        self, builtin: str, required_permissions: Any, *, permanent: bool
    ) -> None:
        self.grants.append((
            builtin,
            tuple(rp.session_pattern for rp in required_permissions),
            permanent,
        ))


class _RecordingTelemetry:
    """Records each telemetry event the adapter sends."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, object]]] = []

    def send_telemetry_event(
        self, name: str, properties: dict[str, object], **_: object
    ) -> None:
        self.sent.append((name, properties))


@pytest.mark.asyncio
async def test_unified_adapter_tracks_open_callbacks_for_delivery_lifecycle(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    class FakeSession:
        session_id = "session-1"
        rejected: object | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, params: object) -> object:
            self.rejected = params
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 1},
                after_response=None,
                on_response_abandoned=None,
            )

    fake_session = FakeSession()
    adapter = _inert_adapter(fake_session, None, str(tmp_path))
    callback = _approval_callback(
        session_id="session-1",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        related_entry_id="effect-action-1",
        created_at=1,
        input_entry_id="user-entry-1",
        required_permissions=(),
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    assert isinstance(adapter.open_callbacks()[0], PublicCallbackEntry)
    assert adapter.open_callbacks()[0].callback_id == "approval-call-1"
    assert adapter.open_callbacks()[0].input_entry_id == "user-entry-1"
    assert adapter.open_callbacks()[0].related_entry_id == "effect-action-1"
    detail = cast(Any, adapter.open_callbacks()[0].detail)
    assert detail.related_entry_id == "effect-action-1"
    assert isinstance(detail.effect, ShellEffectDetail)
    assert detail.effect.input is not None
    assert detail.effect.input.command == "echo hi"

    await adapter.reject_callback_delivery(
        "session-1", "approval-call-1", CallbackResultError(message="not delivered")
    )

    assert fake_session.rejected is not None
    rejected = cast(Any, fake_session.rejected)
    assert rejected.result.callback_id == "approval-call-1"
    assert rejected.result.error.message == "not delivered"


@pytest.mark.asyncio
async def test_unified_adapter_adds_path_scope_choices_to_approval_callbacks(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    class FakeSession:
        session_id = "session-1"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

    adapter = _inert_adapter(FakeSession(), None, str(tmp_path))
    callback = _approval_callback(
        session_id="session-1",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        related_entry_id="effect-action-1",
        created_at=1,
        input_entry_id="user-entry-1",
        required_permissions=(
            cast(
                Any,
                {
                    "scope": "outside_directory",
                    "invocationPattern": "/outside/missing",
                    "sessionPattern": "vibe-path:exact:/outside/missing",
                    "label": "outside workdir (/outside/missing)",
                },
            ),
        ),
    )

    events = adapter._callback_events({
        "type": "callback_requested",
        "callback": callback,
    })

    assert events is not None
    [opened] = adapter.open_callbacks()
    detail = cast(Any, opened.detail)
    assert detail.path_scope_choices == [PathGrantScope.EXACT]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("approve", []),
        ("approve_for_session", [("file_system.bash", ("npm test *",), False)]),
        ("approve_permanently", [("file_system.bash", ("npm test *",), True)]),
        ("deny", []),
    ],
)
async def test_unified_adapter_records_only_an_approval_that_outlives_the_call(
    tmp_path: Path, decision: str, expected: list[Any]
) -> None:
    """*Prepare*: An open approval callback naming the command pattern it needs.
    *Do*: Answer it with each decision the client can send.
    *Assert*: Only the widened approvals are recorded, scoped to that pattern. The
    Runtime collapses every yes to one boolean, so "once" and "for the session"
    are indistinguishable downstream unless the difference is kept here.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback
    from vibe.app_server.protocol import CallbackResult, CallbackResultParams

    # Prepare
    class FakeSession:
        session_id = "session-1"

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, _params: object) -> object:
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 1},
                after_response=None,
                on_response_abandoned=None,
            )

    permissions = _RecordingPermissions()
    adapter = _inert_adapter(
        FakeSession(), None, str(tmp_path), permissions=permissions
    )
    adapter._callback_events({
        "type": "callback_requested",
        "callback": _approval_callback(
            session_id="session-1",
            callback_id="approval-call-1",
            action=_approval_action("turn-1"),
            related_entry_id="effect-action-1",
            created_at=1,
            input_entry_id="user-entry-1",
            required_permissions=(
                cast(
                    Any,
                    {
                        "scope": "command_pattern",
                        "invocationPattern": "npm test",
                        "sessionPattern": "npm test *",
                        "label": "npm test *",
                    },
                ),
            ),
        ),
    })

    # Do
    await adapter.respond_to_callback(
        CallbackResultParams(
            session_id="session-1",
            result=CallbackResult(
                callback_id="approval-call-1",
                output={"type": "approval", "decision": {"type": decision}},
            ),
        )
    )

    # Assert
    assert permissions.grants == expected


@pytest.mark.asyncio
async def test_unified_adapter_forwards_an_unoffered_path_scope_without_granting_it(
    tmp_path: Path,
) -> None:
    """An invalid sticky scope must not leave the Harness callback open."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._session import _approval_callback
    from vibe.app_server.protocol import CallbackResult, CallbackResultParams

    class FakeSession:
        session_id = "session-1"
        received: object | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        async def respond_to_callback(self, params: object) -> object:
            self.received = params
            return SimpleNamespace(
                response={"accepted": True, "last_event_id": 1},
                after_response=None,
                on_response_abandoned=None,
            )

    session = FakeSession()
    permissions = _RecordingPermissions()
    adapter = _inert_adapter(session, None, str(tmp_path), permissions=permissions)
    adapter._callback_events({
        "type": "callback_requested",
        "callback": _approval_callback(
            session_id="session-1",
            callback_id="approval-call-1",
            action=_approval_action("turn-1"),
            related_entry_id="effect-action-1",
            created_at=1,
            input_entry_id="user-entry-1",
            required_permissions=(
                cast(
                    Any,
                    {
                        "scope": "outside_directory",
                        "invocationPattern": "/outside/config.json",
                        "sessionPattern": "vibe-path:exact:/outside/config.json",
                        "label": "outside workdir (/outside/config.json)",
                    },
                ),
            ),
        ),
    })
    params = CallbackResultParams(
        session_id="session-1",
        result=CallbackResult(
            callback_id="approval-call-1",
            output={
                "type": "approval",
                "decision": {
                    "type": "approve_for_session",
                    "pathScope": "directory_recursive",
                },
            },
        ),
    )

    response = await adapter.respond_to_callback(params)

    assert response.response.accepted
    assert session.received == params
    assert permissions.grants == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("classification", "expected", "absent"),
    [
        pytest.param(
            {
                "verdict": "prompt",
                "outcome": "escalated_denied",
                "risk_level": None,
                "user_authorization": None,
                "error_type": "ClassifierTimeout",
            },
            {
                "verdict": "prompt",
                "tool_name": "file_system.bash",
                "outcome": "escalated_denied",
                "escalated": True,
                "error_type": "ClassifierTimeout",
            },
            ["risk_level", "user_authorization", "approval_grant"],
            id="drops-null-fields",
        ),
        pytest.param(
            {"outcome": "escalated_approved", "approval_grant": "approve_for_session"},
            {"outcome": "escalated_approved", "approval_grant": "approve_for_session"},
            [],
            id="forwards-the-approval-grant",
        ),
    ],
)
async def test_classification_event_is_forwarded_to_telemetry_not_the_ui(
    tmp_path: Path,
    classification: dict[str, object],
    expected: dict[str, object],
    absent: list[str],
) -> None:
    """*Prepare*: A Smart Approve classification event from the Runtime, with
        null fields, or with the human's approval grant.
    *Do*: Hand it to the adapter.
    *Assert*: It is consumed as one ``vibe.tool_classification`` telemetry
        event with its fields, null fields dropped, and never reaches the
        client. The grant is the only field that tells a one-shot approval
        from a "remember for the session" one.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _inert_adapter(_RecordingSession(), str(tmp_path), str(tmp_path))
    adapter._telemetry = telemetry = _RecordingTelemetry()

    # Do
    consumed = await adapter._consumed_classification_event({
        "type": "tool_classification",
        "eventId": 7,
        "tool_name": "file_system.bash",
        "verdict": "prompt",
        "tier": "fast",
        "reason": "risky",
        "escalated": True,
        "latency_ms": 12.0,
        "classifier_model": "mistral-small-latest",
        "turn_id": "t1",
        "call_id": "c1",
        **classification,
    })

    # Assert
    assert consumed is True
    [(name, properties)] = telemetry.sent
    assert name == "vibe.tool_classification"
    assert {key: properties.get(key) for key in expected} == expected
    assert [key for key in absent if key in properties] == []


@pytest.mark.asyncio
async def test_non_classification_event_is_not_consumed_as_telemetry(
    tmp_path: Path,
) -> None:
    """A normal session event is not treated as a classification and emits no telemetry."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._telemetry = telemetry = _RecordingTelemetry()

    consumed = await adapter._consumed_classification_event({
        "type": "session_state_updated"
    })

    assert consumed is False
    assert telemetry.sent == []


@pytest.mark.asyncio
async def test_subagent_classification_is_forwarded_not_applied_as_child_state(
    tmp_path: Path,
) -> None:
    """A wrapped subagent classification reaches telemetry, not root projection."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._telemetry = telemetry = _RecordingTelemetry()

    root_state = PublicSessionState(
        event_id=0,
        session=PublicSession(
            id="session-1", status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
    )
    translated, returned_state, watermark = await adapter._translate_child_event(
        {
            "type": "child_session_event",
            "sessionId": "child-1",
            "eventId": 9,
            "event": {
                "type": "tool_classification",
                "eventId": 3,
                "tool_name": "file_system.bash",
                "verdict": "allow",
                "tier": "fast",
                "reason": "read-only",
                "outcome": "auto_approved",
                "escalated": False,
                "latency_ms": 5.0,
                "classifier_model": "mistral-small-latest",
                "turn_id": "t1",
                "call_id": "c1",
            },
        },
        50,
        root_state,
    )

    assert translated == []
    assert returned_state is root_state
    assert watermark == 9
    assert len(telemetry.sent) == 1
    assert telemetry.sent[0][0] == "vibe.tool_classification"
    assert telemetry.sent[0][1]["tool_name"] == "file_system.bash"


@pytest.mark.asyncio
async def test_unified_start_turn_appends_the_body_of_an_invoked_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """Prepare an adapter over a workspace skill.

    Do start a turn whose prompt is ``/code-review``.

    Assert the rendered body rides the message. Core owns model-visible history
    under Unified, so there is no fabricated tool-call pair to inject; the body
    has to travel as content or the slash command does nothing at all.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session, process_services=process_services)
    display = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "shown prompt"}]
    )
    prompt = (
        '/code-review compare <skill_content name="code-review">example</skill_content>'
    )

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text=prompt)],
            user_display_content=display,
        )
    )

    blocks = session.sent[-1].message
    assert blocks[0].text == prompt
    assert "Read the diff twice." in blocks[-1].text
    assert '<skill_content name="code-review">' in blocks[-1].text
    session.preview = f"{prompt} {blocks[-1].text.replace(chr(10), ' ')}"
    adapter._skills = {"code-review": "changed after invocation"}

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == prompt
    assert message.user_display_content == display
    assert resumed.state.session.preview == prompt
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"
    assert isinstance(effect.state, CompletedEffectState)
    assert effect.state.display.message == "skill: code-review"
    assert "Read the diff twice." in effect.state.output_text


@pytest.mark.asyncio
async def test_unified_start_turn_appends_every_skill_mentioned_with_a_slash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _write_workspace_skill(tmp_path, "lint", "Run the linter first.")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session, process_services=process_services)
    prompt = "please apply /lint, then /code-review and /unknown"

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id, message=[TextContentBlock(text=prompt)]
        )
    )

    blocks = session.sent[-1].message
    assert [block.text for block in blocks[:1]] == [prompt]
    assert '<skill_content name="lint">' in blocks[1].text
    assert '<skill_content name="code-review">' in blocks[2].text
    assert len(blocks) == 3

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    message, *effects = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == prompt
    assert message.user_display_content is None
    assert [
        cast(SkillEffectDetail, effect.detail).input
        for effect in effects
        if isinstance(effect, PublicEffectEntry)
    ] == [SkillEffectInput(name="lint"), SkillEffectInput(name="code-review")]
    assert len({effect.id for effect in effects}) == 2
    assert all(effect.related_entry_id == message.id for effect in effects)


@pytest.mark.asyncio
@pytest.mark.parametrize("history_limit", [1, 2])
async def test_unified_read_keeps_bounded_skill_history_pairs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    history_limit: int,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session, process_services=process_services)

    for prompt in ("older message", "/code-review"):
        await adapter.start_turn(
            TurnStartParams(
                session_id=session.session_id, message=[TextContentBlock(text=prompt)]
            )
        )
    resumed = await adapter.read(
        SessionReadParams(
            session_id=session.session_id, history=PageRequest(limit=history_limit)
        )
    )

    assert resumed.state.history is not None
    assert len(resumed.state.history) == history_limit + 1
    message, effect = resumed.state.history[-2:]
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review"
    assert isinstance(effect, PublicEffectEntry)
    assert effect.related_entry_id == message.id
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"


@pytest.mark.asyncio
@pytest.mark.parametrize("already_loaded", [False, True])
async def test_unified_history_projects_a_pre_marker_skill_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    already_loaded: bool,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session, process_services=process_services)
    payload = (
        already_loaded_message("code-review")
        if already_loaded
        else adapter._skills["code-review"]
    )
    session.sent.append(
        SimpleNamespace(
            message=[
                TextContentBlock(text="/code-review please"),
                TextContentBlock(text=payload),
            ]
        )
    )

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )

    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review please"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "code-review"


@pytest.mark.asyncio
async def test_unified_history_hides_mixed_case_already_loaded_skill_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    session = _RecordingSession()
    adapter = await _skill_adapter(tmp_path, session, process_services=process_services)
    session.sent.append(
        SimpleNamespace(
            message=[
                TextContentBlock(text="/MySkill please"),
                TextContentBlock(text=already_loaded_message("MySkill")),
            ]
        )
    )

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )

    assert resumed.state.history is not None
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/MySkill please"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.detail, SkillEffectDetail)
    assert effect.detail.input is not None
    assert effect.detail.input.name == "MySkill"


@pytest.mark.asyncio
async def test_a_switch_turned_on_mid_session_leaves_it_on_the_direct_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A session opened with the Session API switch off.
    *Do*: Turn the switch on, reload the configuration and push it into the
        session.
    *Assert*: The pushed configuration keeps the Session API off, so the
        session's Runtime and its subagents stay on the path it opened with.
    """
    # Prepare
    session = _RecordingSession()
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, session, process_services=process_services
    )

    # Do
    monkeypatch.setenv("VIBE_EXPERIMENTAL_LOCAL_RUNTIME_SESSION_API", "true")
    await adapter._context.config_orchestrator.reload()
    await adapter._apply_derivation()

    # Assert
    assert adapter._context.config_orchestrator.config.experimental_local_runtime_session_api
    assert session.applied
    assert not cast(Any, session.applied[-1]).session_api


@pytest.mark.asyncio
async def test_unified_history_projects_a_skill_body_containing_skill_markup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """Nested marker text in a skill cannot make its outer payload public."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    nested = (
        '<skill_content name="code-review">\n'
        "# Skill: code-review\n\nexample\n"
        "</skill_content>"
    )
    session = _RecordingSession()
    adapter = await _skill_adapter(
        tmp_path,
        session,
        skill_body=f"Document `</skill_content>` and this: {nested}",
        process_services=process_services,
    )

    await adapter.start_turn(
        TurnStartParams(
            session_id=session.session_id,
            message=[TextContentBlock(text="/code-review")],
        )
    )
    blocks = session.sent[-1].message
    session.preview = f"/code-review {blocks[-1].text.replace(chr(10), ' ')}"

    resumed = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert resumed.state.history is not None
    assert resumed.state.session.preview == "/code-review"
    message, effect = resumed.state.history
    assert isinstance(message, PublicMessageEntry)
    assert message.text == "/code-review"
    assert isinstance(effect, PublicEffectEntry)
    assert isinstance(effect.state, CompletedEffectState)
    assert nested in effect.state.output_text

    nested_marker = '<skill_content name="code-review">'
    nested_at = session.preview.rfind(nested_marker)
    assert nested_at >= 0
    session.preview = (
        session.preview[: nested_at + len(nested_marker)] + " # Skill: code-rev…"
    )
    truncated = await adapter.read(
        SessionReadParams(session_id=session.session_id, history=PageRequest(limit=10))
    )
    assert truncated.state.session.preview == "/code-review"


@pytest.mark.asyncio
async def test_unified_read_preserves_open_callbacks_outside_history(
    tmp_path: Path,
) -> None:
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot
    callback = _approval_callback(
        session_id=session.session_id,
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        related_entry_id="effect-action-1",
        created_at=1,
        required_permissions=(),
        input_entry_id=None,
    )
    snapshot = snapshot.model_copy(
        update={
            "state": snapshot.state.model_copy(update={"active_callbacks": [callback]})
        }
    )

    async def read_snapshot(_params: object) -> object:
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    response = await adapter.read(SessionReadParams(session_id=session.session_id))

    callbacks = [
        entry
        for entry in response.state.history or []
        if isinstance(entry, PublicCallbackEntry)
    ]
    assert len(callbacks) == 1
    assert callbacks[0].callback_id == "approval-call-1"
    assert callbacks[0].state.status == "open"
    assert [callback.callback_id for callback in response.state.active_callbacks] == [
        "approval-call-1"
    ]


def test_unified_state_update_carries_the_open_callbacks(tmp_path: Path) -> None:
    """*Prepare*: A Harness state event reporting one open approval callback.
    *Do*: Translate the event into the public session state.
    *Assert*: The public state lists the callback as active, so a snapshot sent
    while the callback is open does not tell the client it is gone.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        BlockedSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import _approval_callback

    callback = _approval_callback(
        session_id="session-root",
        callback_id="approval-call-1",
        action=_approval_action("turn-1"),
        related_entry_id="effect-action-1",
        created_at=1,
        required_permissions=(),
        input_entry_id=None,
    )
    state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id="session-root",
            status=BlockedSessionStatus(
                active_turn_id="turn-1",
                callback_id="approval-call-1",
                callback_kind="approval",
            ),
            created_at=1,
            updated_at=1,
        ),
        active_callbacks=[callback],
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None), str(tmp_path), str(tmp_path)
    )

    current, _watermark = adapter._updated_state(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 1,
            "state": state.model_dump(mode="json", by_alias=True),
        },
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
        started_by_session_id=None,
    )

    assert [callback.callback_id for callback in current.active_callbacks] == [
        "approval-call-1"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("watermark", [2, 20])
async def test_unified_read_keeps_the_cursor_of_its_snapshot(
    tmp_path: Path, watermark: int
) -> None:
    """A read waits for its snapshot, without adopting later notifications' cursor."""
    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot.model_copy(
        update={"watermark": watermark}
    )
    read_started = asyncio.Event()

    async def read_snapshot(_params: object) -> object:
        read_started.set()
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._events_subscribed = True
    adapter._event_id = 5
    adapter._observed_harness_watermark = watermark - 1
    reading = asyncio.create_task(
        adapter.read(SessionReadParams(session_id="session-1"))
    )
    await read_started.wait()
    assert not reading.done()

    # One Harness event fans out into three public notifications. Deliver the
    # following event as well, before the waiting read gets scheduled again.
    adapter._event_id = 8
    await adapter._mark_harness_event_observed(watermark)
    adapter._event_id = 11
    await adapter._mark_harness_event_observed(watermark + 1)

    response = await asyncio.wait_for(reading, timeout=1)
    assert response.state.event_id == response.last_event_id == 8
    assert adapter._event_id == 11
    assert not adapter._pending_reads


@pytest.mark.asyncio
@pytest.mark.parametrize("close_stream", [False, True])
async def test_unified_abandoned_read_releases_its_watermark(
    tmp_path: Path, close_stream: bool
) -> None:
    session = _RecordingSession()
    snapshot = (await session.read(None)).snapshot.model_copy(update={"watermark": 2})
    read_started = asyncio.Event()

    async def read_snapshot(_params: object) -> object:
        read_started.set()
        return SimpleNamespace(snapshot=snapshot)

    session.read = read_snapshot
    adapter = _inert_adapter(session, str(tmp_path), str(tmp_path))
    adapter._events_subscribed = True
    reading = asyncio.create_task(
        adapter.read(SessionReadParams(session_id="session-1"))
    )
    await read_started.wait()
    if close_stream:
        await adapter._finish_event_stream()
        with pytest.raises(SessionBackendError) as raised:
            await reading
        assert raised.value.code is ProtocolErrorCode.STALE_CURSOR
    else:
        reading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reading
    await adapter._mark_harness_event_observed(2)
    assert not adapter._pending_reads


class _CompactingSession(_RecordingSession):
    def __init__(self) -> None:
        super().__init__()
        self.watermark = 0
        self.completed: list[tuple[int, str]] = []
        self.events_started = asyncio.Event()
        self.events: asyncio.Queue[harness_session_protocol.JsonObject] = (
            asyncio.Queue()
        )

    async def read(self, _params: object) -> harness_session_protocol.SessionReadResult:
        return harness_session_protocol.SessionReadResult(
            snapshot=harness_session_protocol.SessionSnapshot(
                state=harness_session_protocol.PublicSessionState(
                    session=harness_session_protocol.PublicSession(
                        id=self.session_id,
                        status=harness_session_protocol.IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=harness_session_protocol.TurnQueue(
                        items=[], paused=False, max_items=32
                    ),
                ),
                history_limit=1,
                watermark=self.watermark,
            )
        )

    async def subscribe(self, params: object) -> HarnessSessionSubscription:
        async def events() -> AsyncIterator[harness_session_protocol.JsonObject]:
            self.events_started.set()
            while True:
                yield await self.events.get()

        return HarnessSessionSubscription(
            snapshot=(await self.read(params)).snapshot, events=events()
        )

    async def compact(self, params: object) -> _SessionBackendResult:
        self.watermark += 1
        watermark = self.watermark
        state = (await self.read(params)).snapshot.state.model_dump(
            mode="json", by_alias=True
        )
        event: harness_session_protocol.JsonObject = {
            "type": "session_state_updated",
            "state": state,
            "eventId": watermark,
        }

        def after_response() -> None:
            self.completed.append((watermark, "sent"))
            self.events.put_nowait(event)

        return _SessionBackendResult(
            response={"summary": "summary", "state": state, "last_event_id": watermark},
            buffered_events=(event,),
            after_response=after_response,
            on_response_abandoned=lambda: self.completed.append((
                watermark,
                "abandoned",
            )),
        )


@pytest.mark.asyncio
async def test_unified_stale_compaction_callbacks_leave_current_response_pending(
    tmp_path: Path,
) -> None:
    """*Prepare*: A settled compaction followed by a second pending response.
    *Do*: Repeat both callbacks from the first response before settling the second.
    *Assert*: Only the second response can release its Harness events.
    """
    # Prepare
    session = _CompactingSession()
    adapter = _stats_adapter(tmp_path, session)
    params = SessionCompactParams(session_id=session.session_id)
    first = await adapter.compact(params)
    assert first.after_response is not None
    assert first.on_response_abandoned is not None
    first.after_response()
    second = await adapter.compact(params)

    # Do
    first.after_response()
    first.on_response_abandoned()

    # Assert
    assert session.completed == [(1, "sent")]
    assert second.after_response is not None
    second.after_response()
    assert session.completed == [(1, "sent"), (2, "sent")]


@pytest.mark.asyncio
@pytest.mark.parametrize("try_second_compaction", [False, True])
async def test_unified_abandonment_does_not_block_pending_compaction_response(
    tmp_path: Path, try_second_compaction: bool
) -> None:
    """*Prepare*: A subscribed Session with compaction events awaiting its response.
    *Do*: Abandon an edit and flush both responses before releasing compaction.
    *Assert*: Both flushes complete and ordinary event flushing resumes afterward.
    """
    # Prepare
    session = _CompactingSession()
    adapter = _stats_adapter(tmp_path, session)
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=session.session_id)
    )

    async def consume_events() -> None:
        async for _event in subscription.events:
            pass

    consumer = asyncio.create_task(consume_events())
    await asyncio.wait_for(session.events_started.wait(), timeout=1)
    compaction = await adapter.compact(
        SessionCompactParams(session_id=session.session_id)
    )
    assert compaction.after_response is not None
    try:
        # Do
        if try_second_compaction:
            with pytest.raises(SessionBackendError) as exc_info:
                await adapter.compact(
                    SessionCompactParams(session_id=session.session_id)
                )
            assert exc_info.value.code is ProtocolErrorCode.CONFLICT
        await adapter.abandon_queued_turn_edit(
            TurnQueueEditAbandonParams(
                session_id=session.session_id,
                queue_item_id="queue-1",
                edit_id=UUID("11111111-1111-4111-8111-111111111111"),
            )
        )
        await asyncio.wait_for(adapter.flush_events(), timeout=1)
        await asyncio.wait_for(adapter.flush_events(), timeout=1)

        # Assert
        assert session.completed == []
        compaction.after_response()
        await asyncio.wait_for(adapter.flush_events(), timeout=1)
        assert session.completed == [(1, "sent")]
        assert not consumer.done()
    finally:
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer


@pytest.mark.asyncio
async def test_unified_flush_events_does_not_wait_before_event_stream_starts(
    tmp_path: Path,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class FakeHarnessSession:
        session_id = "session-1"
        started_by_session_id: str | None = None
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(1)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                if False:
                    yield {}

            return HarnessSessionSubscription(
                snapshot=self._snapshot(0), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=watermark,
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))
    await asyncio.wait_for(adapter.flush_events(), timeout=0.1)
    await cast(Any, subscription.events).aclose()


@pytest.mark.asyncio
async def test_unified_flush_events_tracks_queue_event_watermarks(
    tmp_path: Path,
) -> None:
    """*Prepare*: A queue event whose watermark is newer than the subscription snapshot.
    *Do*: Consume the event while `flush_events` waits for the Harness watermark.
    *Assert*: The queue event advances the observed watermark before the stream closes.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        Event as HarnessEvent,
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
        TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class FakeHarnessSession:
        session_id = "session-1"
        started_by_session_id: str | None = None
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def __init__(self) -> None:
            self.events_started = asyncio.Event()
            self.release_events = asyncio.Event()
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(1)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events() -> AsyncIterator[dict[str, Any]]:
                self.events_started.set()
                yield HarnessEvent(
                    event_id="1",
                    emitted_at=1,
                    session_id=self.session_id,
                    payload=HarnessTurnQueueUpdatedEvent(queue=self.turn_queue),
                ).model_dump(mode="json", by_alias=True)
                await self.release_events.wait()

            return HarnessSessionSubscription(
                snapshot=self._snapshot(0), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=self.turn_queue,
                ),
                history_limit=1,
                watermark=watermark,
            )

    fake_session = FakeHarnessSession()
    adapter = _inert_adapter(fake_session, str(tmp_path), str(tmp_path))
    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))

    async def consume_events() -> None:
        async for _event in subscription.events:
            pass

    consumer = asyncio.create_task(consume_events())
    await asyncio.wait_for(fake_session.events_started.wait(), timeout=1)

    # Do
    await asyncio.wait_for(adapter.flush_events(), timeout=1)

    # Assert
    assert not consumer.done()
    fake_session.release_events.set()
    await asyncio.wait_for(consumer, timeout=1)


@pytest.mark.asyncio
async def test_unified_adapter_preserves_canonical_queue_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """*Prepare*: A fake Unified session exposing the canonical queue contract.
    *Do*: Enqueue and read through the Vibe adapter, then consume its queue event.
    *Assert*: The adapter preserves queue IDs and complete canonical entries.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    from mistralai_vibe_local_harness.session_protocol import (
        Event as HarnessEvent,
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        QueuedTurn as HarnessQueuedTurn,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnContextInputEntry as HarnessTurnContextInputEntry,
        TurnEnqueueResponse as HarnessTurnEnqueueResponse,
        TurnQueue as HarnessTurnQueue,
        TurnQueueReadResponse as HarnessTurnQueueReadResponse,
        TurnQueueReplaceParams as HarnessTurnQueueReplaceParams,
        TurnQueueReplaceResponse as HarnessTurnQueueReplaceResponse,
        TurnQueueUpdatedEvent as HarnessTurnQueueUpdatedEvent,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    class Result:
        def __init__(self, response: object) -> None:
            self.response = response
            self.after_response = None

    class FakeHarnessSession:
        session_id = "session-1"
        started_by_session_id: str | None = None
        cwd: str | None = None

        has_active_work = False
        turn_waits = False

        def __init__(self) -> None:
            self._pins: dict[SessionPin, str | None] = {}
            self.received: object | None = None
            self.replaced: Any | None = None
            self.turn_queue = HarnessTurnQueue(items=[], paused=False, max_items=32)

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, pin: SessionPin) -> str | None:
            return self._pins.get(pin)

        async def persist_pin(self, pin: SessionPin, value: str | None) -> bool:
            if self._pins.get(pin) == value:
                return False
            self._pins[pin] = value
            return True

        async def enqueue_turn(self, params: Any) -> Result:
            self.received = params
            self.turn_queue = HarnessTurnQueue(
                items=[
                    HarnessQueuedTurn(
                        id="queue-1", created_at=2, entries=[*params.entries]
                    )
                ],
                paused=False,
                max_items=32,
            )
            return Result(HarnessTurnEnqueueResponse(queue_item_id="queue-1"))

        async def replace_queued_turn(self, params: Any) -> Result:
            self.replaced = params
            current = self.turn_queue.items[0]
            self.turn_queue = HarnessTurnQueue(
                items=[
                    current.model_copy(update={"entries": [*params.entries]}, deep=True)
                ],
                paused=False,
                max_items=32,
            )
            return Result(
                HarnessTurnQueueReplaceResponse(queue_item_id=params.queue_item_id)
            )

        async def read_turn_queue(self, _params: object) -> Result:
            return Result(HarnessTurnQueueReadResponse(queue=self.turn_queue))

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield HarnessEvent(
                    event_id="1",
                    emitted_at=1,
                    session_id=self.session_id,
                    payload=HarnessTurnQueueUpdatedEvent(queue=self.turn_queue),
                ).model_dump(mode="json", by_alias=True)

            snapshot = HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=self.turn_queue,
                ),
                history_limit=50,
                watermark=0,
            )
            return HarnessSessionSubscription(snapshot=snapshot, events=events())

    fake_session = FakeHarnessSession()
    adapter, _ = await _unified_adapter_with_real_context(
        tmp_path, fake_session, process_services=process_services
    )
    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))

    # Do
    display = UserDisplayContent(
        version="1", host="vibe", content=[{"type": "text", "text": "shown prompt"}]
    )
    enqueued = await adapter.enqueue_turn(
        TurnEnqueueParams(
            session_id="session-1",
            entries=[
                TurnContextInputEntry(
                    entry_id="context-1",
                    content=[SessionTextContentBlock(text="injected context")],
                ),
                TurnUserInputEntry(
                    entry_id="message-1",
                    content=[SessionTextContentBlock(text="hello")],
                    annotations=MessageAnnotations.model_validate({
                        "vibe.userDisplayContent": display
                    }),
                ),
            ],
        )
    )
    replaced = await adapter.replace_queued_turn(
        TurnQueueReplaceParams(
            idempotency_key="edit-1",
            session_id="session-1",
            queue_item_id="queue-1",
            entries=[
                TurnContextInputEntry(
                    entry_id="context-2",
                    content=[SessionTextContentBlock(text="edited context")],
                )
            ],
        )
    )
    read = await adapter.read_turn_queue(TurnQueueReadParams(session_id="session-1"))
    event = await anext(subscription.events)

    # Assert
    assert enqueued.response == TurnEnqueueResponse(queue_item_id="queue-1")
    assert replaced.response == TurnQueueReplaceResponse(queue_item_id="queue-1")
    received = cast(Any, fake_session.received)
    assert isinstance(received.entries[0], HarnessTurnContextInputEntry)
    assert received.entries[0].entry_id == "context-1"
    assert received.entries[0].content[0].text == "injected context"
    assert received.entries[1].entry_id == "message-1"
    assert received.entries[1].content[0].text == "hello"
    assert received.entries[1].annotations.vibe_user_display_content.model_dump(
        mode="json"
    ) == display.model_dump(mode="json")
    assert fake_session.replaced is not None
    replaced_params = fake_session.replaced
    assert isinstance(replaced_params, HarnessTurnQueueReplaceParams)
    assert replaced_params.queue_item_id == "queue-1"
    assert cast(Any, replaced_params).entries[0].content[0].text == "edited context"
    assert read.response.queue.items[0].model_dump(mode="json") == {
        "id": "queue-1",
        "createdAt": 2,
        "entries": [
            {
                "role": "context",
                "entryId": "context-2",
                "content": [{"type": "text", "text": "edited context"}],
                "annotations": {},
            }
        ],
    }
    assert event.method == "turn/queueUpdated"
    assert cast(Any, event.params).queue == read.response.queue
    await cast(Any, subscription.events).aclose()


@pytest.mark.asyncio
async def test_unified_state_updates_preserve_the_subscription_history_window(
    tmp_path: Path,
) -> None:
    """A full-state update must not replay history omitted from the subscription."""
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    def message(index: int) -> JsonObject:
        return {
            "type": "message",
            "id": f"entry-{index}",
            "sessionId": "session-root",
            "createdAt": index,
            "updatedAt": index,
            "generationStatus": "completed",
            "role": "user",
            "content": [{"type": "text", "text": f"message {index}"}],
        }

    def state(entries: list[JsonObject], updated_at: int) -> HarnessPublicSessionState:
        return HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=updated_at,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )

    entries = [message(index) for index in range(3)]
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(
            state=state(entries[-2:], 1), history_limit=2, watermark=1
        )
    ).state

    replayed, current, _ = await adapter._state_update_events(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 2,
            "state": state(entries, 2).model_dump(mode="json", by_alias=True),
        },
        previous,
        2,
    )

    assert not [
        event for event in replayed if isinstance(event.event, HistoryEntryAdded)
    ]
    assert [entry.id for entry in current.history or []] == ["entry-1", "entry-2"]

    added, current, _ = await adapter._state_update_events(
        {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": 3,
            "state": state([*entries, message(3)], 3).model_dump(
                mode="json", by_alias=True
            ),
        },
        current,
        2,
    )

    added_entries = [
        event.event.entry
        for event in added
        if isinstance(event.event, HistoryEntryAdded)
    ]
    assert [entry.id for entry in added_entries] == ["entry-3"]
    assert [entry.id for entry in current.history or []] == ["entry-2", "entry-3"]


@pytest.mark.asyncio
async def test_unified_state_updates_reproject_only_changed_entries(
    tmp_path: Path,
) -> None:
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    def message(entry_id: str, role: str, text: str, status: str) -> JsonObject:
        return {
            "type": "message",
            "id": entry_id,
            "sessionId": "session-root",
            "createdAt": 1,
            "updatedAt": 1,
            "generationStatus": status,
            "role": role,
            "content": [{"type": "text", "text": text}],
        }

    def state(entries: list[JsonObject], updated_at: int) -> HarnessPublicSessionState:
        return HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=updated_at,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )

    def update(event_id: int, entries: list[JsonObject]) -> dict[str, object]:
        return {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": event_id,
            "state": state(entries, event_id).model_dump(mode="json", by_alias=True),
        }

    question = message("question", "user", "Stream please", "completed")
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(
            state=state([question], 1), history_limit=10, watermark=1
        )
    ).state

    # Do
    _, first, _ = await adapter._state_update_events(
        update(2, [question, message("answer", "assistant", "Hello", "in_progress")]),
        previous,
        10,
    )
    answer = message("answer", "assistant", "Hello there", "in_progress")
    events, second, _ = await adapter._state_update_events(
        update(3, [question, answer]), first, 10
    )

    # Assert
    assert (second.history or [])[0] is (first.history or [])[0]
    assert [
        event.event.entry.id
        for event in events
        if isinstance(event.event, HistoryEntryUpdated)
    ] == ["answer"]
    full_read = adapter._read_response(
        HarnessSessionSnapshot(
            state=state([question, answer], 3), history_limit=10, watermark=3
        )
    ).state
    assert second.history == full_read.history


def test_history_projection_recognises_a_rebuilt_entry_by_identity() -> None:
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _HistoryProjections

    comparisons = 0

    class CountingEntry(dict[str, object]):
        def __eq__(self, other: object) -> bool:
            nonlocal comparisons
            comparisons += 1
            return super().__eq__(other)

    entry: JsonObject = {
        "type": "message",
        "id": "question",
        "sessionId": "session-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "completed",
        "role": "user",
        "content": [{"type": "text", "text": "Stream please"}],
    }
    projections = _HistoryProjections()
    first = projections.project([entry], ())
    rebuilt = CountingEntry(entry)

    # Do
    second = projections.project([rebuilt], ())
    third = projections.project([rebuilt], ())

    # Assert
    assert comparisons == 1
    assert third[0] is second[0] is first[0]


def test_history_projection_reprojects_when_the_display_roots_change(
    tmp_path: Path,
) -> None:
    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _HistoryProjections

    path = tmp_path / "repo" / "README.md"
    entry: JsonObject = {
        "type": "effect",
        "id": "effect-1",
        "sessionId": "session-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": None,
        "title": "file_system.read_file",
        "detail": {
            "kind": "tool",
            "toolName": "file_system.read_file",
            "input": {"path": str(path)},
            "display": {"summary": "file_system.read_file", "statusText": "Running"},
        },
        "state": {"status": "running", "outputText": ""},
    }
    projections = _HistoryProjections()

    # Do
    under_repo = projections.project([entry], (tmp_path / "repo",))
    under_tmp = projections.project([entry], (tmp_path,))

    # Assert
    messages = [
        entry.detail.display.message
        for entry in (*under_repo, *under_tmp)
        if isinstance(entry, PublicEffectEntry)
    ]
    assert messages == ["README.md", str(Path("repo", "README.md"))]


@pytest.mark.asyncio
async def test_unified_compact_keeps_session_loops(tmp_path: Path) -> None:
    """*Prepare*: A session with an active loop and a pending compaction.
    *Do*: Compact the session.
    *Assert*: The translated compaction update keeps the session's loops, so
    clients never receive a snapshot with an empty loop list.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # Prepare
    from vibe.app_server._unified_harness_backend_adapter import _project_scheduled_loop

    session = _CompactingSession()
    adapter = _stats_adapter(tmp_path, session)
    loop = ScheduledPrompt(
        id="loop-1",
        interval_seconds=3600,
        prompt="Check the CI pipeline",
        next_fire_at=1_800_000_000.0,
        created_at=1_700_000_000.0,
    )
    await adapter.replace_scheduled_loops([loop])

    # Do
    await adapter.compact(SessionCompactParams(session_id=session.session_id))

    # Assert
    assert len(adapter._pretranslated_events) == 1
    _events, current = next(iter(adapter._pretranslated_events.values()))
    assert current.session.loops == [_project_scheduled_loop(loop)]


@pytest.mark.asyncio
async def test_unified_root_subscription_translates_child_session_events(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root Harness subscription registers a child before its first update.
    *Do*: Consume the child update through the root adapter event stream.
    *Assert*: Root-owned summary events retain child identity and lifecycle state.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        InProgressPublicTurn,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        RunningSessionStatus,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    root_id = "session-root"
    child_id = "session-child"
    root_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=root_id, status=IdleSessionStatus(), created_at=1, updated_at=1
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    child_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=child_id,
            root_session_id=root_id,
            parent_session_id=root_id,
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    running_child = child_state.model_copy(
        update={
            "session": child_state.session.model_copy(
                update={
                    "status": RunningSessionStatus(active_turn_id="child-turn-1"),
                    "updated_at": 2,
                }
            ),
            "latest_turn": InProgressPublicTurn(
                id="child-turn-1", session_id=child_id, started_at=2
            ),
        }
    )

    class FakeHarnessSession:
        session_id = root_id
        started_by_session_id: str | None = None
        parent_session_id = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "child_session_registered",
                    "sessionId": child_id,
                    "eventId": 1,
                    "snapshot": HarnessSessionSnapshot(
                        state=child_state, history_limit=20, watermark=0
                    ).model_dump(mode="json", by_alias=True),
                }
                yield {
                    "type": "child_session_event",
                    "sessionId": child_id,
                    "eventId": 2,
                    "event": {
                        "type": "session_state_updated",
                        "sessionId": child_id,
                        "eventId": 1,
                        "state": running_child.model_dump(mode="json", by_alias=True),
                    },
                }

            return HarnessSessionSubscription(
                snapshot=HarnessSessionSnapshot(
                    state=root_state, history_limit=20, watermark=0
                ),
                events=events(),
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    # Do
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    registration = await anext(subscription.events)
    event = await anext(subscription.events)

    # Assert
    assert isinstance(registration.event, ChildSessionUpdated)
    assert registration.event.child_session.id == child_id
    assert registration.session_id == root_id
    assert registration.event_id == 1
    assert isinstance(event.event, ChildSessionUpdated)
    assert event.event.child_session.id == child_id
    assert event.event.child_session.status.type == "running"
    assert event.session_id == root_id
    assert event.event_id == 2
    assert event.params is not None
    assert event.params.model_dump(by_alias=True)["sessionId"] == root_id
    assert subscription.snapshot.state.child_sessions == []
    await cast(Any, subscription.events).aclose()

    resumed = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    assert len(resumed.snapshot.state.child_sessions) == 1
    assert resumed.snapshot.state.child_sessions[0].id == child_id
    assert resumed.snapshot.state.child_sessions[0].status.type == "running"
    await cast(Any, resumed.events).aclose()


def _persisted_child_record(
    agent_name: str,
    child_session_id: str,
    state: Any,
    *,
    agent_type: str | None = "explore",
) -> Any:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.vibe._subagents._models import ChildSessionRecord

    return ChildSessionRecord(
        agent_name=agent_name,
        agent_type=agent_type,
        child_session_id=child_session_id,
        spawn_action_id=f"{agent_name}-spawn",
        spawn_request_digest=f"{agent_name}-request",
        template_digest=f"{agent_name}-template",
        policy_ceiling_digest=f"{agent_name}-ceiling",
        state=state,
    )


def _persisted_child_states() -> dict[str, Any]:
    """One child record per lifecycle type, keyed by its ``type`` discriminator."""
    from mistralai_vibe_local_harness.vibe._subagents._models import (
        ChildGenerationRef,
        ChildTombstone,
        CompletedChildTurnOutcome,
        CreationFailedChild,
        DeletingIdleChild,
        FailedChildTurnOutcome,
        IdleChild,
        RunningChild,
        SubagentFailure,
        TurnFailedChild,
    )

    completed = IdleChild(
        last_outcome=CompletedChildTurnOutcome(
            generation=1,
            turn_id="turn-1",
            completed_at_unix_ms=1,
            output=[],
            final_answer="done",
        )
    )
    failure = SubagentFailure(code="spawn_failed", message="no store", retryable=False)
    return {
        "idle": completed,
        "running": RunningChild(
            active=ChildGenerationRef(generation=1, turn_id="turn-1")
        ),
        "turn_failed": TurnFailedChild(
            outcome=FailedChildTurnOutcome(
                generation=1, turn_id="turn-1", completed_at_unix_ms=1, failure=failure
            ),
            reusable=True,
        ),
        "tombstone": ChildTombstone(),
        "deleting_idle": DeletingIdleChild(),
        "creation_failed": CreationFailedChild(failure=failure),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("state_type", ["idle", "running", "turn_failed"])
async def test_unified_adapter_seeds_persisted_children_into_the_resume_state(
    tmp_path: Path, state_type: str
) -> None:
    """*Prepare*: A root adapter whose Host holds one persisted child record.
    *Do*: Seed the persisted child states, then read the root.
    *Assert*: The read state lists the child with the record's identity.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    child = _RecordingSession()
    child.session_id = "session-child"
    child.sent.append(SimpleNamespace(message=[TextContentBlock(text="child")]))

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()[state_type]
                ),
            )

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return (
                root_session_id == "session-1" and child_session_id == "session-child"
            )

        async def read(self, params: Any) -> object:
            result = await child.read(params)
            return SimpleNamespace(snapshot=result.snapshot, cwd=str(tmp_path))

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    await adapter.seed_persisted_child_states()
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )

    # Assert
    assert [summary.id for summary in read.state.child_sessions] == ["session-child"]
    seeded = read.state.child_sessions[0]
    assert seeded.name == "test-audit"
    assert seeded.agent_type == "explore"
    assert seeded.status.type == "idle"
    assert adapter.references_child("session-child")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state_type", ["tombstone", "deleting_idle", "creation_failed"]
)
async def test_unified_adapter_does_not_seed_deleted_or_failed_children(
    tmp_path: Path, state_type: str
) -> None:
    """*Prepare*: A root adapter whose Host holds one unseedable child record.
    *Do*: Seed the persisted child states.
    *Assert*: The record is skipped; only live-comparable children are listed.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()[state_type]
                ),
            )

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    await adapter.seed_persisted_child_states()

    # Assert
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_resume_survives_an_unreadable_child_store(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A root adapter whose Host holds a child with no readable store.
    *Do*: Seed the persisted child states.
    *Assert*: The child is skipped with a warning instead of failing the resume.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            return (
                _persisted_child_record(
                    "test-audit", "session-child", _persisted_child_states()["idle"]
                ),
            )

        async def read(self, _params: Any) -> object:
            raise RuntimeError("child store is gone")

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.seed_persisted_child_states()

    # Assert
    assert "session-child" in caplog.text
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_resume_survives_a_host_error_enumerating_children(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A root adapter whose Host fails to enumerate child records.
    *Do*: Seed the persisted child states.
    *Assert*: The enumeration warning is logged and the resume stays empty.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == "session-1"
            raise RuntimeError("cannot enumerate children")

    adapter = _inert_adapter(
        _RecordingSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )

    # Do
    with caplog.at_level(logging.WARNING, logger="vibe"):
        await adapter.seed_persisted_child_states()

    # Assert
    assert "Skipping persisted child summaries" in caplog.text
    read = await adapter.read(
        SessionReadParams(session_id="session-1", history=PageRequest(limit=20))
    )
    assert read.state.child_sessions == []


@pytest.mark.asyncio
async def test_unified_adapter_live_registration_overwrites_a_seeded_child(
    tmp_path: Path,
) -> None:
    """*Prepare*: A root adapter seeded with a persisted idle child.
    *Do*: Subscribe, then let the Host register the live child again.
    *Assert*: The registration overwrites the seeded row with the live state.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    root_id = "session-root"
    child_id = "session-child"
    seeded_state = HarnessPublicSessionState(
        session=HarnessPublicSession(
            id=child_id,
            root_session_id=root_id,
            parent_session_id=root_id,
            status=IdleSessionStatus(),
            created_at=1,
            updated_at=1,
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    live_state = seeded_state.model_copy(
        update={"session": seeded_state.session.model_copy(update={"updated_at": 2})}
    )
    seeded_snapshot = HarnessSessionSnapshot(
        state=seeded_state, history_limit=20, watermark=0
    )

    class FakeHarnessSession:
        session_id = root_id
        started_by_session_id: str | None = None
        parent_session_id = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "child_session_registered",
                    "sessionId": child_id,
                    "eventId": 1,
                    "snapshot": HarnessSessionSnapshot(
                        state=live_state, history_limit=20, watermark=0
                    ).model_dump(mode="json", by_alias=True),
                }

            return HarnessSessionSubscription(
                snapshot=HarnessSessionSnapshot(
                    state=HarnessPublicSessionState(
                        session=HarnessPublicSession(
                            id=root_id,
                            status=IdleSessionStatus(),
                            created_at=1,
                            updated_at=1,
                        ),
                        turn_queue=HarnessTurnQueue(
                            items=[], paused=False, max_items=32
                        ),
                    ),
                    history_limit=20,
                    watermark=0,
                ),
                events=events(),
            )

    class FakeHost:
        def child_session_records(self, root_session_id: str) -> tuple[Any, ...]:
            assert root_session_id == root_id
            return (
                _persisted_child_record(
                    "test-audit", child_id, _persisted_child_states()["idle"]
                ),
            )

        def references_child(self, root_session_id: str, child_session_id: str) -> bool:
            return root_session_id == root_id and child_session_id == child_id

        async def read(self, _params: Any) -> object:
            return SimpleNamespace(snapshot=seeded_snapshot, cwd=str(tmp_path))

    adapter = _inert_adapter(
        FakeHarnessSession(), str(tmp_path), str(tmp_path), host=FakeHost()
    )
    await adapter.seed_persisted_child_states()
    assert adapter._child_states[child_id].session.updated_at == 1

    # Do
    subscription = await adapter.subscribe(
        SessionReadParams(session_id=root_id, history=PageRequest(limit=20))
    )
    registration = await anext(subscription.events)

    # Assert
    assert isinstance(registration.event, ChildSessionUpdated)
    assert registration.event.child_session.id == child_id
    assert adapter._child_states[child_id].session.updated_at == 2
    await cast(Any, subscription.events).aclose()


@pytest.mark.asyncio
async def test_unified_subagent_analytics_emits_one_content_free_terminal_event(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: A Unified wait effect transitions from running to a timed-out result.
    *Do*: Reconcile the terminal Harness snapshot twice through the Vibe adapter.
    *Assert*: One existing tool-finished event exposes only approved dimensions.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )

    effect = {
        "type": "effect",
        "id": "effect-wait-1",
        "sessionId": "session-root",
        "turnId": "turn-root",
        "createdAt": 1,
        "updatedAt": 1,
        "generationStatus": "in_progress",
        "relatedEntryId": None,
        "title": "subagent.wait",
        "detail": {
            "kind": "tool",
            "toolName": "subagent.wait",
            "input": {
                "agentName": "secret-agent-name",
                "timeoutMs": 10,
                "prompt": "secret prompt",
            },
            "display": {
                "summary": "Waiting for secret-agent-name",
                "statusText": "Waiting",
            },
        },
        "state": {"status": "running", "outputText": ""},
    }
    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    running = HarnessPublicSessionState(
        session=session,
        history=LatestPublicHistoryPage(entries=[effect]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    terminal_effect = {
        **effect,
        "updatedAt": 2,
        "generationStatus": "completed",
        "state": {
            "status": "completed",
            "output": {
                "type": "success",
                "content": [],
                "structured_content": {
                    "type": "error",
                    "error": "subagent_wait_timeout: secret timeout detail",
                },
            },
            "outputText": "",
            "durationMs": 1,
            "display": {"success": True, "message": "Wait completed"},
        },
    }
    terminal = HarnessPublicSessionState(
        session=session.model_copy(update={"updated_at": 2}),
        history=LatestPublicHistoryPage(entries=[terminal_effect]),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(state=running, history_limit=20, watermark=0)
    ).state
    event = {
        "type": "session_state_updated",
        "sessionId": "session-root",
        "eventId": 1,
        "state": terminal.model_dump(mode="json", by_alias=True),
    }

    # Do
    _, current, _ = await adapter._state_update_events(event, previous, 20)
    await adapter._state_update_events({**event, "eventId": 2}, current, 20)

    # Assert
    assert len(telemetry_events) == 1
    assert telemetry_events[0]["event_name"] == "vibe.tool_call_finished"
    properties = telemetry_events[0]["properties"]
    assert properties["harness_backend"] == "unified"
    assert properties["tool_name"] == "subagent.wait"
    assert properties["status"] == "failure"
    assert properties["subagent_operation"] == "wait"
    assert properties["subagent_outcome"] == "timeout"
    assert properties["subagent_depth"] == 1
    serialized = repr(properties)
    assert "secret-agent-name" not in serialized
    assert "secret prompt" not in serialized
    assert "secret timeout detail" not in serialized


@pytest.mark.asyncio
async def test_model_calls_count_only_the_sessions_own_agent_calls(
    tmp_path: Path,
) -> None:
    """*Prepare*: A session in a turn, and the runtime reporting two agent calls
    for that turn, one for a subagent's turn and a compaction.
    *Do*: Reconcile the session's next state.
    *Assert*: The stats count the session's own two agent calls only.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        InProgressPublicTurn,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe import RequestSentTelemetry

    # Prepare
    session = HarnessPublicSession(
        id="session-root", status=IdleSessionStatus(), created_at=1, updated_at=1
    )
    in_turn = HarnessPublicSessionState(
        session=session,
        latest_turn=InProgressPublicTurn(
            id="turn-root", session_id="session-root", started_at=1
        ),
        turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
    )
    previous = adapter._read_response(
        HarnessSessionSnapshot(state=in_turn, history_limit=20, watermark=0)
    ).state
    event = {
        "type": "session_state_updated",
        "sessionId": "session-root",
        "eventId": 1,
        "state": in_turn.model_dump(mode="json", by_alias=True),
    }
    _, previous, _ = await adapter._state_update_events(event, previous, 20)
    reported: list[tuple[Literal["agent", "compaction"], str]] = [
        ("agent", "turn-root"),
        ("agent", "child-turn"),
        ("compaction", "turn-root"),
        ("agent", "turn-root"),
    ]
    for purpose, turn_id in reported:
        adapter._context.request_sent.record(
            RequestSentTelemetry(
                model="m1",
                purpose=purpose,
                iteration=0,
                nb_context_chars=1,
                nb_context_messages=1,
                nb_prompt_chars=1,
                turn_id=turn_id,
            )
        )

    # Do
    await adapter._state_update_events({**event, "eventId": 2}, previous, 20)

    # Assert
    assert adapter._runtime.stats.model_calls == 2


def test_context_gauge_retained_across_a_usageless_snapshot(
    tmp_path: Path, telemetry_events: list[dict[str, Any]]
) -> None:
    """*Prepare*: a known pre-compaction size, then a snapshot with no usage reading.
    *Do*: Reconcile the usage-less snapshot, then an automatic compaction.
    *Assert*: the size is retained (not zeroed), so the auto event reports it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        LatestPublicHistoryPage,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        TurnQueue as HarnessTurnQueue,
    )

    def _event(entries: list[JsonObject], event_id: int) -> JsonObject:
        # A session with no context_usage set is the usage-less case.
        state = HarnessPublicSessionState(
            session=HarnessPublicSession(
                id="session-root",
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=1,
            ),
            history=LatestPublicHistoryPage(entries=entries),
            turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
        )
        return {
            "type": "session_state_updated",
            "sessionId": "session-root",
            "eventId": event_id,
            "state": state.model_dump(mode="json", by_alias=True),
        }

    telemetry = TelemetryClient(
        config_getter=lambda: build_test_vibe_config(enable_telemetry=True),
        harness_backend=ExperimentSurface.UNIFIED,
    )
    adapter = _inert_adapter(
        _session_stub(session_id="session-root", cwd=None),
        str(tmp_path),
        str(tmp_path),
        runtime=_priced_runtime(),
        telemetry_client=telemetry,
    )
    adapter._context_tokens_before = 4321

    adapter._updated_state(
        _event([], 1),
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
        started_by_session_id=None,
    )
    # The usage-less snapshot must not wipe the last reading.
    assert adapter._context_tokens_before == 4321

    adapter._updated_state(
        _event([_compaction_checkpoint("auto-ret", "automatic", succeeded=True)], 2),
        20,
        cwd=str(tmp_path),
        metadata=None,
        projections=adapter._history_projections,
        started_by_session_id=None,
    )
    triggered = [
        e for e in telemetry_events if e["event_name"] == "vibe.auto_compact_triggered"
    ]
    assert len(triggered) == 1
    assert triggered[0]["properties"]["nb_context_tokens_before"] == 4321


@pytest.mark.asyncio
async def test_unified_flush_events_returns_after_an_event_carrying_a_signal(
    tmp_path: Path,
) -> None:
    """A notice spends an event id, so the flush after it must see that id.

    ``plugin/reload`` publishes one and is answered through the same flush, so
    a signal the forwarder does not record hangs the request that published it.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._session import HarnessSessionSubscription

    stream_open = asyncio.Event()

    class FakeHarnessSession:
        session_id = "session-1"
        started_by_session_id: str | None = None
        cwd: str | None = None

        def configure_turn_settlement(self, settle: object) -> None:
            """Unused: no queued turn is promoted here."""

        def pin(self, _pin: SessionPin) -> str | None:
            return None

        async def read(self, _params: object) -> object:
            return type("ReadResult", (), {"snapshot": self._snapshot(2)})()

        async def subscribe(self, _params: object) -> HarnessSessionSubscription:
            async def events():
                yield {
                    "type": "notice",
                    "level": "warning",
                    "message": "a plugin source is gone",
                    "eventId": 2,
                }
                await stream_open.wait()

            return HarnessSessionSubscription(
                snapshot=self._snapshot(1), events=events()
            )

        def _snapshot(self, watermark: int) -> HarnessSessionSnapshot:
            return HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=watermark,
            )

    adapter = _inert_adapter(FakeHarnessSession(), str(tmp_path), str(tmp_path))

    subscription = await adapter.subscribe(SessionReadParams(session_id="session-1"))
    forwarded: list[Any] = []
    delivered = asyncio.Event()

    async def forward() -> None:
        async for event in subscription.events:
            forwarded.append(event)
            delivered.set()

    forwarder = asyncio.create_task(forward())
    try:
        await asyncio.wait_for(delivered.wait(), timeout=1)
        await asyncio.wait_for(adapter.flush_events(), timeout=1)
    finally:
        stream_open.set()
        await forwarder

    assert [event.method for event in forwarded] == ["warning"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notice", "code"),
    [
        (
            {
                "type": "notice",
                "level": "warning",
                "code": "request_too_long",
                "message": "The request was too long; compacting and retrying.",
            },
            "request_too_long",
        ),
        (
            {
                "type": "notice",
                "level": "warning",
                "message": "The request was too long; compacting and retrying.",
            },
            "warning",
        ),
    ],
)
async def test_unified_notice_shows_as_a_warning_with_its_core_code(
    tmp_path: Path, notice: dict[str, object], code: str
) -> None:
    """*Prepare*: A Harness notice, with a Core warning code or without one.
    *Do*: Translate it through the Unified Harness adapter.
    *Assert*: The Client gets a warning carrying the Core code, else the level.
    """
    adapter = _inert_adapter(_RecordingSession(), None, str(tmp_path))

    translated = await adapter._signal_event(
        {**notice, "eventId": 7}, session_id="session-1"
    )

    assert translated is not None
    event, event_id = translated
    assert event_id == 7
    assert event.method == "warning"
    assert isinstance(event.event, ServerWarning)
    assert event.event.params.warning.code == code
    assert (
        event.event.params.warning.message
        == "The request was too long; compacting and retrying."
    )


@pytest.mark.asyncio
async def test_unified_resume_seeds_persisted_children_into_the_read_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A persisted root session whose harness Host holds one idle child.
    *Do*: Resume the root through a fresh host adapter.
    *Assert*: The resumed backend's read state lists the seeded child.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from mistralai_vibe_local_harness.session_protocol import (
        IdleSessionStatus,
        PublicSession as HarnessPublicSession,
        PublicSessionState as HarnessPublicSessionState,
        SessionSnapshot as HarnessSessionSnapshot,
        TurnQueue as HarnessTurnQueue,
    )
    from mistralai_vibe_local_harness.vibe._host import HarnessSessionReadResult

    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    first_host = _harness_backend_host(config)
    started = await first_host.start(SessionStartParams())
    session_id = started.backend.session_id
    await started.backend.start_turn(
        TurnStartParams(
            session_id=session_id, message=[TextContentBlock(text="persist me")]
        )
    )
    await first_host.shutdown()

    child_id = f"{session_id}-scout"
    second_host = _harness_backend_host(config)
    harness_host = cast(Any, second_host)._host
    monkeypatch.setattr(
        harness_host,
        "child_session_records",
        lambda root_session_id: (
            (
                _persisted_child_record(
                    "scout", child_id, _persisted_child_states()["idle"]
                ),
            )
            if root_session_id == session_id
            else ()
        ),
    )
    original_read = harness_host.read

    async def read_with_child(params: Any) -> Any:
        if params.session_id != child_id:
            return await original_read(params)
        return HarnessSessionReadResult(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=child_id,
                        root_session_id=session_id,
                        parent_session_id=session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=1,
                watermark=0,
            ),
            cwd=str(tmp_path),
            started_by_session_id=None,
        )

    monkeypatch.setattr(harness_host, "read", read_with_child)

    # Do
    resumed = await second_host.resume(SessionResumeParams(session_id=session_id))
    read = await resumed.backend.read(SessionReadParams(session_id=session_id))

    # Assert
    assert [child.id for child in read.state.child_sessions] == [child_id]
    seeded = read.state.child_sessions[0]
    assert seeded.name == "scout"
    assert seeded.agent_type == "explore"
    assert seeded.status.type == "idle"
    assert cast(Any, resumed.backend)._child_states.keys() == {child_id}
    await second_host.shutdown()


class _SubscribingSession(_RecordingSession):
    def __init__(
        self,
        usage: tuple[int, int] | None,
        context_usage: tuple[int, int] | None = None,
        *,
        cached: int = 0,
    ) -> None:
        super().__init__()
        self._usage = usage
        self._context_usage = context_usage
        self._cached = cached

    async def subscribe(self, _params: Any) -> Any:
        from mistralai_vibe_local_harness.session_protocol import (
            IdleSessionStatus as HarnessIdleSessionStatus,
            LatestPublicHistoryPage,
            PublicSession as HarnessPublicSession,
            PublicSessionState as HarnessPublicSessionState,
            SessionSnapshot as HarnessSessionSnapshot,
            TokenUsage as HarnessTokenUsage,
            TurnQueue as HarnessTurnQueue,
        )

        async def events() -> AsyncIterator[Any]:
            return
            yield

        return SimpleNamespace(
            snapshot=HarnessSessionSnapshot(
                state=HarnessPublicSessionState(
                    session=HarnessPublicSession(
                        id=self.session_id,
                        status=HarnessIdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                        token_usage=(
                            None
                            if self._usage is None
                            else HarnessTokenUsage(
                                input_tokens=self._usage[0],
                                output_tokens=self._usage[1],
                                total_tokens=sum(self._usage),
                                cached_input_tokens=self._cached,
                            )
                        ),
                        context_usage=(
                            None
                            if self._context_usage is None
                            else HarnessTokenUsage(
                                input_tokens=self._context_usage[0],
                                output_tokens=self._context_usage[1],
                                total_tokens=sum(self._context_usage),
                            )
                        ),
                    ),
                    history=LatestPublicHistoryPage(entries=[]),
                    turn_queue=HarnessTurnQueue(items=[], paused=False, max_items=32),
                ),
                history_limit=0,
                watermark=0,
            ),
            events=events(),
        )


@pytest.mark.asyncio
async def test_unified_child_retry_state_emits_compatibility_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A registered child session before, during, and after a provider retry.
    *Do*: Translate the child's retry start and clear state updates.
    *Assert*: Retry start emits a child notification without exposing child snapshots.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path)
    idle = _usage_state(turn=PublicTurnStatus.IN_PROGRESS)
    child_id = "session-child"
    idle = idle.model_copy(
        update={
            "session": idle.session.model_copy(update={"id": child_id}),
            "turns": [
                turn.model_copy(update={"session_id": child_id})
                for turn in idle.turns or []
            ],
        }
    )
    retry = PublicRetryState(
        turn_id="turn-1", category=PublicRetryCategory.RATE_LIMITED, detail="HTTP 429"
    )
    retrying = idle.model_copy(update={"retrying": retry})
    adapter._child_states[child_id] = idle
    states = iter(((retrying, 1), (idle, 2)))
    monkeypatch.setattr(
        adapter, "_updated_state", lambda *_args, **_kwargs: next(states)
    )
    wrapper = {
        "type": "child_session_event",
        "sessionId": child_id,
        "eventId": 1,
        "event": {"type": "session_state_updated", "sessionId": child_id},
    }

    # Do
    started, root, _ = await adapter._translate_child_event(wrapper, 20, _usage_state())
    cleared, _final, _ = await adapter._translate_child_event(
        {**wrapper, "eventId": 2}, 20, root
    )

    # Assert
    notices = [event for event in started if event.method == "turn/retrying"]
    assert len(notices) == 1
    assert isinstance(notices[0].event, TurnRetrying)
    assert notices[0].session_id == child_id
    assert notices[0].event.params.category is PublicRetryCategory.RATE_LIMITED
    assert notices[0].event_id is None
    assert not any(isinstance(event.event, SessionSnapshot) for event in started)
    assert cleared == []
    assert adapter._child_states[child_id].retrying is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "expected"),
    [
        pytest.param(
            lambda: _SubscribingSession((4000, 900), cached=3000),
            {
                "session_prompt_tokens": 4000,
                "session_completion_tokens": 900,
                "session_cached_tokens": 3000,
                "last_turn_total_tokens": 0,
                "context_tokens": 0,
            },
            id="no-context-reading",
        ),
        pytest.param(
            lambda: _SubscribingSession((303_000, 75), (102_000, 25)),
            {"session_prompt_tokens": 303_000, "context_tokens": 102_025},
            id="measured-context",
        ),
    ],
)
async def test_unified_stats_seed_from_a_resumed_sessions_usage(
    tmp_path: Path, session: Callable[[], _SubscribingSession], expected: dict[str, int]
) -> None:
    """*Prepare*: A resumed session whose read reports running token totals,
        with or without the context its last call measured.
    *Do*: Subscribe to it.
    *Assert*: The stats start from those totals and are priced; the context
        gauge starts at the measured context, else at zero.
    """
    # Prepare
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    adapter = _stats_adapter(tmp_path, session=session())

    # Do
    await adapter.subscribe(SessionReadParams(session_id=_RecordingSession.session_id))

    # Assert
    stats = adapter.runtime_updated_params().runtime.stats
    assert {key: getattr(stats, key) for key in expected} == expected
    assert stats.input_price_per_million > 0
