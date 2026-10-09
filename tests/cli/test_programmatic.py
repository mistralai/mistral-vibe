from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_config
from tests.mock.mock_backend_factory import mock_backend_factory
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.client import AppServerConnectionClosed
from vibe.app_server.events import HistoryEntryAdded
from vibe.app_server.local import ClientDescriptor, LocalHarnessOptions
from vibe.app_server.models import (
    AgentStatsSnapshot,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicMessageEntry,
    TeleportComplete,
    TextContentBlock,
    TurnErrorCode,
    TurnStop,
)
from vibe.app_server.protocol import ClientInfo, SessionOptions
from vibe.app_server.run_export import RunExport, RunLimits, RunOutcome
from vibe.app_server.session import AppServerSession
from vibe.cli import programmatic
from vibe.cli.headless_run import HeadlessRun, StopRequests
from vibe.cli.programmatic import (
    OutputFormat,
    ProgrammaticOutput,
    ProgrammaticTeleportError,
    _turn_error_outcome,
    run_programmatic,
)
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.types import Backend


def _options() -> LocalHarnessOptions:
    # These tests exercise the legacy programmatic lane end to end with the
    # mock backend factory; the unflagged default is now the Unified Harness,
    # so the legacy lane is selected explicitly.
    return LocalHarnessOptions(
        legacy_harness=True,
        client=ClientDescriptor(
            info=ClientInfo(
                name="vibe_programmatic", version="test", entrypoint="programmatic"
            )
        ),
        session_options=SessionOptions(
            agent=BuiltinAgentName.AUTO_APPROVE,
            disabled_tools=["ask_user_question", "exit_plan_mode"],
            headless=True,
        ),
    )


def _stop() -> StopRequests:
    return StopRequests(RunLimits())


def _use_runtime_config(
    monkeypatch: pytest.MonkeyPatch, config: VibeConfigSchema
) -> None:
    async def build_orchestrator(
        data: dict[str, object] | None = None, *, harness_files: HarnessFilesManager
    ) -> ConfigOrchestrator[VibeConfigSchema]:
        del harness_files
        base = OverridesLayer(data=config.model_dump(mode="json"), name="base")
        session = OverridesLayer(data=data or {})
        return await ConfigOrchestrator.create(
            schema=VibeConfigSchema,
            layers=[base, session],
            default_layer_resolver=lambda: base,
        )

    monkeypatch.setattr(
        "vibe.app_server._runtime.build_default_orchestrator", build_orchestrator
    )


def test_streaming_output_uses_public_history_entries(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    telemetry_events: list[dict],
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend(
            mock_llm_chunk(content="Decorators wrap functions.")
        ),
    ):
        report = run_programmatic(
            harness_options=_options(),
            prompt="Explain decorators",
            output_format=OutputFormat.STREAMING,
            stop=_stop(),
        )

    assert report.result.outcome is RunOutcome.FINISHED
    assert report.final_response is None
    entries = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    messages = [entry for entry in entries if entry["type"] == "message"]
    assert [(entry["role"], entry["content"][0]["text"]) for entry in messages] == [
        ("user", "Explain decorators"),
        ("assistant", "Decorators wrap functions."),
    ]
    assert not any(entry["role"] == "system" for entry in messages)

    new_sessions = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.new_session"
    ]
    assert len(new_sessions) == 1
    assert new_sessions[0]["properties"]["agent_entrypoint"] == "programmatic"

    closed_sessions = [
        event
        for event in telemetry_events
        if event.get("event_name") == "vibe.session_closed"
    ]
    assert len(closed_sessions) == 1


def test_text_output_returns_last_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Understood.")]),
    ):
        report = run_programmatic(
            harness_options=_options(),
            prompt="Continue",
            output_format=OutputFormat.TEXT,
            stop=_stop(),
        )

    assert report.result.outcome is RunOutcome.FINISHED
    assert report.final_response == "Understood."


@pytest.mark.parametrize(
    ("limits", "outcome"),
    [
        (RunLimits(max_tokens=1), RunOutcome.TOKEN_LIMIT),
        (RunLimits(max_price=0.0), RunOutcome.PRICE_LIMIT),
    ],
    ids=["tokens", "price"],
)
def test_a_spent_budget_wins_over_a_finished_turn(
    monkeypatch: pytest.MonkeyPatch, limits: RunLimits, outcome: RunOutcome
) -> None:
    """*Prepare*: A budget the reply goes past, and a turn that sees no usage
    update before it ends, as when the last update lands after the reply.
    *Do*: Run the turn to its end.
    *Assert*: The run reports the spent budget, not a finished turn.
    """
    # Prepare
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    act = programmatic._act

    async def act_without_usage(
        session: AppServerSession,
        prompt: str,
        output: ProgrammaticOutput,
        _stop_requests: StopRequests,
    ) -> None:
        await act(session, prompt, output, _stop())

    monkeypatch.setattr(programmatic, "_act", act_without_usage)

    # Do
    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Done.")]),
    ):
        report = run_programmatic(
            harness_options=_options(),
            prompt="Finish",
            output_format=OutputFormat.TEXT,
            stop=StopRequests(limits),
        )

    # Assert
    assert report.final_response == "Done."
    assert report.result.outcome is outcome
    assert report.result.outcome.exit_code == 3


@pytest.mark.parametrize("then_fails", [False, True], ids=["finishes", "fails"])
def test_an_abort_wins_over_however_the_turn_then_ends(
    monkeypatch: pytest.MonkeyPatch, then_fails: bool
) -> None:
    """*Prepare*: A turn during which the agent socket's server aborts the
    run, and which then finishes or fails.
    *Do*: Run it.
    *Assert*: The run reports the abort with the server's message, under the
    session it started.
    """
    # Prepare
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    act = programmatic._act

    async def act_then_abort(
        session: AppServerSession,
        prompt: str,
        output: ProgrammaticOutput,
        stop_requests: StopRequests,
    ) -> None:
        stop_requests.abort("The task's budget is gone")
        if then_fails:
            raise RuntimeError("The sandbox is gone")
        await act(session, prompt, output, stop_requests)

    monkeypatch.setattr(programmatic, "_act", act_then_abort)

    # Do
    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Done.")]),
    ):
        report = run_programmatic(
            harness_options=_options(),
            prompt="Finish",
            output_format=OutputFormat.TEXT,
            stop=_stop(),
        )

    # Assert
    assert report.result.outcome is RunOutcome.ABORTED
    assert report.result.outcome.exit_code == 4
    assert report.result.error is not None
    assert report.result.error.message == "The task's budget is gone"
    assert report.result.session_id is not None


def test_only_the_first_stop_request_counts() -> None:
    """*Do*: Abort a run after it spent its token budget, and abort another
    twice, then spend its budget.
    *Assert*: The first request stands, and only an abort that counts keeps
    its message.
    """
    spent = AgentStatsSnapshot(session_prompt_tokens=10)
    late_abort = StopRequests(RunLimits(max_tokens=1))
    late_abort.observe_usage(spent)
    late_abort.abort("too late")

    aborted = StopRequests(RunLimits(max_tokens=1))
    aborted.abort("first")
    aborted.abort("second")
    aborted.observe_usage(spent)

    assert late_abort.requested is RunOutcome.TOKEN_LIMIT
    assert late_abort.abort_error is None
    assert aborted.requested is RunOutcome.ABORTED
    assert aborted.abort_error is not None
    assert aborted.abort_error.message == "first"


@pytest.mark.parametrize(
    ("limits", "outcome"),
    [
        (RunLimits(max_tokens=10), RunOutcome.TOKEN_LIMIT),
        (RunLimits(max_price=10.0), RunOutcome.PRICE_LIMIT),
    ],
    ids=["tokens", "price"],
)
def test_budgets_count_from_the_run_start(
    limits: RunLimits, outcome: RunOutcome
) -> None:
    """*Prepare*: A resumed session that had spent 100 tokens at a dollar per
    million before the run, against a budget of 10 tokens or 10 dollars.
    *Do*: Count the run's usage from there, then observe the session spend 5,
    then 11 more tokens, priced so that 11 tokens cost more than 10 dollars.
    *Assert*: The run's usage is what it spent itself; the budget holds at 5
    and is spent at 11.
    """
    # Prepare
    price = 1_000_000.0
    earlier = AgentStatsSnapshot(
        session_prompt_tokens=60,
        session_completion_tokens=40,
        input_price_per_million=price,
        output_price_per_million=price,
    )
    stop_requests = StopRequests(limits)

    # Do
    stop_requests.count_usage_from(earlier)
    within = earlier.model_copy(update={"session_completion_tokens": 45})
    stop_requests.observe_usage(within)
    held = stop_requests.requested
    usage = stop_requests.run_usage(within)
    stop_requests.observe_usage(
        earlier.model_copy(update={"session_completion_tokens": 51})
    )

    # Assert
    assert (usage.session_prompt_tokens, usage.session_completion_tokens) == (0, 5)
    assert usage.session_cost == 5.0
    assert held is None
    assert stop_requests.requested is outcome


def _assistant_message(entry_id: str, turn_id: str, text: str) -> PublicMessageEntry:
    return PublicMessageEntry(
        id=entry_id,
        session_id="session-1",
        turn_id=turn_id,
        created_at=0,
        updated_at=0,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        role="assistant",
        content=[TextContentBlock(text=text)],
    )


@pytest.mark.parametrize(
    ("this_run", "printed"),
    [([], None), ([_assistant_message("e2", "turn-2", "Now.")], "Now.")],
    ids=["no-reply", "reply"],
)
def test_text_output_answers_only_from_this_run(
    this_run: list[PublicHistoryEntry], printed: str | None
) -> None:
    """*Prepare*: A resumed session whose earlier run replied.
    *Do*: Start the run's output, then finalize it with or without a reply of
    its own.
    *Assert*: It prints this run's reply, or none, never the earlier one.
    """
    # Prepare
    earlier: list[PublicHistoryEntry] = [_assistant_message("e1", "turn-1", "Before.")]
    output = ProgrammaticOutput(OutputFormat.TEXT, StringIO())

    # Do
    output.start(earlier)
    answer = output.finalize([*earlier, *this_run])

    # Assert
    assert answer == printed


def test_streaming_output_skips_earlier_runs() -> None:
    """*Prepare*: A resumed session whose earlier run replied.
    *Do*: Start the run's streaming output, then add this run's reply.
    *Assert*: Only this run's reply is written.
    """
    # Prepare
    stream = StringIO()
    output = ProgrammaticOutput(OutputFormat.STREAMING, stream)
    reply = _assistant_message("e2", "turn-2", "Now.")

    # Do
    output.start([_assistant_message("e1", "turn-1", "Before.")])
    output.consume(HistoryEntryAdded(entry=reply))

    # Assert
    assert [json.loads(line)["id"] for line in stream.getvalue().splitlines()] == ["e2"]


def test_json_teleport_output_includes_the_result_url() -> None:
    stream = StringIO()
    output = ProgrammaticOutput(OutputFormat.JSON, stream)

    output.consume_teleport(
        TeleportComplete(operation_id="teleport-1", url="https://vibe.example/run")
    )
    output.finalize([])

    assert json.loads(stream.getvalue()) == {
        "history": [],
        "teleportUrl": "https://vibe.example/run",
    }


def test_untrusted_workspace_warning_comes_from_app_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("project instructions", encoding="utf-8")
    monkeypatch.chdir(project)
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)

    with mock_backend_factory(
        Backend.MISTRAL,
        lambda provider, **kwargs: FakeBackend([mock_llm_chunk(content="Done.")]),
    ):
        run_programmatic(harness_options=_options(), prompt="Continue", stop=_stop())

    warning = capsys.readouterr().err
    assert str(project) in warning
    assert "AGENTS.md" in warning
    assert "--trust" in warning


def test_conversation_limits_cross_the_public_turn_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    options = _options()
    options = replace(
        options,
        session_options=options.session_options.model_copy(update={"max_turns": 0}),
    )

    with mock_backend_factory(
        Backend.MISTRAL, lambda provider, **kwargs: FakeBackend()
    ):
        report = run_programmatic(
            harness_options=options, prompt="Continue", stop=_stop()
        )

    assert report.result.outcome is RunOutcome.TURN_LIMIT
    assert report.result.stop_reason is TurnStop.LIMIT
    assert "Turn limit" in (report.final_response or "")


def test_teleport_flag_always_runs_teleport(monkeypatch: pytest.MonkeyPatch) -> None:
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    teleport = AsyncMock()
    monkeypatch.setattr("vibe.cli.programmatic._teleport", teleport)

    report = run_programmatic(
        harness_options=_options(),
        prompt="Hello",
        output_format=OutputFormat.TEXT,
        teleport=True,
        stop=_stop(),
    )

    assert report.result.outcome is RunOutcome.FINISHED
    assert report.final_response is None
    teleport.assert_awaited_once()


@pytest.mark.parametrize(
    ("code", "outcome"),
    [
        (TurnErrorCode.INVALID_API_KEY, RunOutcome.CONFIG_ERROR),
        (TurnErrorCode.INVALID_MODEL, RunOutcome.CONFIG_ERROR),
        (TurnErrorCode.RESPONSE_TOO_LONG, RunOutcome.LENGTH),
        (TurnErrorCode.CONTEXT_TOO_LONG, RunOutcome.LENGTH),
        (TurnErrorCode.REFUSAL, RunOutcome.REFUSAL),
        (TurnErrorCode.BACKEND_ERROR, RunOutcome.INFRASTRUCTURE_FAILURE),
        (TurnErrorCode.RATE_LIMIT, RunOutcome.INFRASTRUCTURE_FAILURE),
        (None, RunOutcome.INFRASTRUCTURE_FAILURE),
    ],
)
def test_failed_turn_codes_map_to_outcomes(
    code: str | None, outcome: RunOutcome
) -> None:
    assert _turn_error_outcome(code) is outcome


class _UnresponsiveSession:
    """A session whose turn, interrupt, refresh and close never return."""

    session_id = "session-unresponsive"

    def __init__(self) -> None:
        self.history: list[object] = []
        self.state = SimpleNamespace(turns=[])
        runtime = SimpleNamespace(
            wait_until_ready=AsyncMock(),
            refresh=_hang,
            stats=AgentStatsSnapshot(),
            issues=[],
            session_log=SimpleNamespace(path=None),
        )
        workspace = SimpleNamespace(
            trust_status=AsyncMock(
                return_value=SimpleNamespace(status="trusted", details=None)
            )
        )
        config = SimpleNamespace(
            read_effective=AsyncMock(return_value=SimpleNamespace(config={}))
        )
        self.resources = SimpleNamespace(
            runtime=runtime, workspace=workspace, config=config
        )

    async def act(self, _prompt: str) -> AsyncIterator[object]:
        await _hang()
        yield

    async def interrupt(self) -> None:
        await _hang()

    async def close(self) -> None:
        await _hang()


async def _hang() -> None:
    await asyncio.Event().wait()


def test_a_stopped_run_exits_within_one_grace_period_when_nothing_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A session whose turn, interrupt, refresh and close all hang,
    a short grace period and a time limit.
    *Do*: Run until the time limit stops it.
    *Assert*: The run reports the deadline within about one grace period, not
    one per step it waited on.
    """
    # Prepare
    grace_seconds = 0.5
    monkeypatch.setattr(
        "vibe.cli.programmatic.STOP_GRACE_SECONDS", grace_seconds, raising=True
    )
    session = _UnresponsiveSession()
    monkeypatch.setattr(
        "vibe.cli.programmatic.LocalHarness",
        lambda _options: SimpleNamespace(start=AsyncMock(return_value=session)),
    )
    stop = StopRequests(RunLimits(time_limit_s=0.1))
    started = time.monotonic()

    # Do
    report = run_programmatic(harness_options=_options(), prompt="Go", stop=stop)

    # Assert
    elapsed = time.monotonic() - started
    assert report.result.outcome is RunOutcome.DEADLINE
    assert elapsed < 0.1 + grace_seconds * 1.6


class _DroppedSession:
    """A session whose app-server connection drops in the middle of its turn.

    Once the connection is gone for good, a runtime refresh fails as the real
    session's does, with a plain ``RuntimeError``, and closing fails too. Only
    client-side state is left.
    """

    session_id = "session-dropped"

    def __init__(self, session_dir: Path) -> None:
        self.history: list[object] = []
        self.state = SimpleNamespace(turns=[])
        runtime = SimpleNamespace(
            wait_until_ready=AsyncMock(),
            refresh=AsyncMock(
                side_effect=RuntimeError("App-server connection is closed")
            ),
            stats=AgentStatsSnapshot(),
            issues=[],
            session_log=SimpleNamespace(path=str(session_dir)),
        )
        workspace = SimpleNamespace(
            trust_status=AsyncMock(
                return_value=SimpleNamespace(status="trusted", details=None)
            )
        )
        config = SimpleNamespace(
            read_effective=AsyncMock(return_value=SimpleNamespace(config={}))
        )
        self.resources = SimpleNamespace(
            runtime=runtime, workspace=workspace, config=config
        )
        self.close = AsyncMock(
            side_effect=AppServerConnectionClosed("App server connection closed")
        )

    async def act(self, _prompt: str) -> AsyncIterator[object]:
        # The turn's usage reached the client before the connection dropped.
        self.resources.runtime.stats = AgentStatsSnapshot(
            session_prompt_tokens=7, session_completion_tokens=3
        )
        yield object()
        raise AppServerConnectionClosed("App server connection closed")


def test_a_connection_drop_mid_turn_still_exports_the_session_and_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A session with a journal on disk whose connection drops after
    the turn's first event.
    *Do*: Run it, then end the headless run with an output directory.
    *Assert*: Exit 2, and the export still has the session, its usage and the
    copied journal.
    """
    # Prepare
    session_dir = tmp_path / "session-dropped"
    session_dir.mkdir()
    (session_dir / "messages.jsonl").write_text('{"role": "user"}\n')
    session = _DroppedSession(session_dir)
    monkeypatch.setattr(
        "vibe.cli.programmatic.LocalHarness",
        lambda _options: SimpleNamespace(start=AsyncMock(return_value=session)),
    )
    output_dir = tmp_path / "out"
    run = HeadlessRun(output_dir=output_dir, stop=_stop())

    # Do
    report = run_programmatic(harness_options=_options(), prompt="Go", stop=run.stop)
    with pytest.raises(SystemExit) as exited:
        run.exit(report)

    # Assert
    assert exited.value.code == 2
    export = RunExport.model_validate_json(
        (output_dir / "export.json").read_text(encoding="utf-8")
    )
    assert export.outcome is RunOutcome.INFRASTRUCTURE_FAILURE
    assert export.error is not None
    assert export.error.message == "App server connection closed"
    assert export.session_id == "session-dropped"
    assert export.usage is not None
    assert export.usage.input_tokens == 7
    assert export.journal_dir == "session"
    assert (output_dir / "session" / "messages.jsonl").is_file()
    session.close.assert_awaited_once()


def test_a_teleport_error_still_reaches_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A teleport that fails once the session is up.
    *Do*: Run with teleport on.
    *Assert*: The teleport error reaches the caller, which reports it as a
    usage error, rather than ending the run as an infrastructure failure.
    """
    # Prepare
    config = build_test_vibe_config(
        include_model_info=False, include_commit_signature=False
    )
    _use_runtime_config(monkeypatch, config)
    monkeypatch.setattr(
        "vibe.cli.programmatic._teleport",
        AsyncMock(side_effect=ProgrammaticTeleportError("No project")),
    )

    # Do / Assert
    with pytest.raises(ProgrammaticTeleportError, match="No project"):
        run_programmatic(
            harness_options=_options(), prompt="Hello", teleport=True, stop=_stop()
        )
