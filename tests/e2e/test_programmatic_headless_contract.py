"""The headless `vibe -p` contract, driven as a process against the mock server.

Each test runs the installed `vibe` executable on the default (Unified Harness)
runtime and checks what a caller can observe: the exit code, the export written
to `--output-dir`, the journal copied beside it, and the requests the model
endpoint received.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any

import pytest

from tests.e2e.agent_loop_characterization.support import (
    assistant_text_chunks,
    single_tool_call_chunks,
)
from tests.e2e.common import (
    VIBE_EXECUTABLE,
    export_config,
    read_export,
    read_export_config,
    run_vibe_headless,
    write_e2e_config,
)
from tests.e2e.mock_server import ChatCompletionsRequestPayload, StreamingMockServer
from vibe import __version__
from vibe.app_server.run_export import RunUsage

pytestmark = [pytest.mark.timeout(90), pytest.mark.usefixtures("setup_e2e_env")]

# Counts from process start, so it must cover startup and the tool's launch on a
# loaded machine; otherwise the deadline lands before the tool exists.
_TIME_LIMIT_S = 8


def _text_reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return assistant_text_chunks("All done.")


def _endless_tool_calls(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return single_tool_call_chunks(
        call_id=f"call_{request_index}",
        tool_name="bash",
        arguments={"command": "echo again"},
    )


def _user_texts(payload: ChatCompletionsRequestPayload) -> list[str]:
    return [
        str(message.get("content", ""))
        for message in payload.get("messages", [])
        if message.get("role") == "user"
    ]


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_finished_run_exports_usage_config_and_journal_and_exits_zero(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A prompt file and a model that answers in one message.
    *Do*: Run `vibe -p` with the prompt file and an output directory.
    *Assert*: Exit 0, the prompt reached the model, and the export and journal land
    in the output directory.
    """
    # Prepare
    prompt_file = tmp_path / "task.md"
    prompt_file.write_text("Summarise the repository layout.\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        ["--prompt-file", str(prompt_file), "--output-dir", str(output_dir)],
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert "All done." in result.stdout
    assert any(
        "Summarise the repository layout." in text
        for text in _user_texts(streaming_mock_server.requests[0])
    )
    export = read_export(output_dir)
    assert export.vibe_version == __version__
    assert export.outcome == "finished"
    assert export.exit_code == 0
    assert export.stop_reason is None
    assert export.error is None
    assert export.usage == RunUsage(
        input_tokens=3, output_tokens=4, cached_input_tokens=0, total_tokens=7
    )
    assert export.cost_usd == 0.0
    assert export_config(export)["active_model"] == "mock-model"
    assert export.session_id
    assert export.journal_dir == "session"
    assert (output_dir / "session" / "journal").is_dir()


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_export_config_is_the_config_the_session_ran_with(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: Command-line choices that change the session's config: an
    agent that bypasses tool approval and an enabled-tools list.
    *Do*: Run `vibe -p` with them and an output directory.
    *Assert*: The exported config carries them, as the server resolved them.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        [
            "-p",
            "Hello",
            "--auto-approve",
            "--enabled-tools",
            "grep",
            "--output-dir",
            str(output_dir),
        ],
    )

    # Assert
    assert result.returncode == 0, result.stderr
    config = read_export_config(output_dir)
    assert config["bypass_tool_permissions"] is True
    assert config["enabled_tools"] == ["grep"]
    assert "ask_user_question" in config["disabled_tools"]


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_a_headless_session_does_not_offer_cron(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model that answers with text.
    *Do*: Run `vibe -p`.
    *Assert*: The model is offered tools, but not `cron`.
    """
    # Do
    result = run_vibe_headless(
        e2e_workdir, ["-p", "Hello", "--output-dir", str(tmp_path / "out")]
    )

    # Assert
    assert result.returncode == 0, result.stderr
    request: dict[str, Any] = dict(streaming_mock_server.requests[0])
    names = [tool["function"]["name"] for tool in request["tools"]]
    assert names
    assert not [name for name in names if "cron" in name], names


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
def test_prompt_from_stdin_finishes(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model that answers in one message.
    *Do*: Pipe the prompt on stdin to `vibe -p` with no prompt text.
    *Assert*: Exit 0 and the piped prompt reached the model.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir, ["-p", "--output-dir", str(output_dir)], stdin="Piped task text"
    )

    # Assert
    assert result.returncode == 0, result.stderr
    assert any(
        "Piped task text" in text
        for text in _user_texts(streaming_mock_server.requests[0])
    )
    assert read_export(output_dir).outcome == "finished"


@pytest.mark.parametrize("streaming_mock_server", [_endless_tool_calls], indirect=True)
def test_turn_limit_exhaustion_is_a_limit_outcome_and_exits_three(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model that keeps calling a tool.
    *Do*: Run `vibe -p` with `--max-turns 2`.
    *Assert*: Exit 3 and the export reports the turn limit.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        [
            "-p",
            "Keep going",
            "--auto-approve",
            "--max-turns",
            "2",
            "--output-dir",
            str(output_dir),
        ],
    )

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "turn_limit"
    assert export.exit_code == 3
    assert export.stop_reason == "limit"
    assert export.limits.max_turns == 2
    assert len(streaming_mock_server.requests) == 2


@pytest.mark.parametrize("streaming_mock_server", [_endless_tool_calls], indirect=True)
@pytest.mark.parametrize(
    ("budget_flag", "budget", "outcome"),
    [("--max-tokens", "10", "token_limit"), ("--max-price", "10", "price_limit")],
)
def test_spent_budget_stops_the_turn_and_exits_three(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    tmp_path: Path,
    budget_flag: str,
    budget: str,
    outcome: str,
) -> None:
    """*Prepare*: A model that keeps calling a tool, 7 tokens and $7 per request.
    *Do*: Run `vibe -p` with a token or price budget of 10.
    *Assert*: Exit 3 and the export reports that budget, spent past its limit.
    """
    # Prepare
    write_e2e_config(
        Path(os.environ["VIBE_HOME"]),
        streaming_mock_server.api_base,
        model_settings=["input_price = 1000000.0", "output_price = 1000000.0"],
    )
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        [
            "-p",
            "Keep going",
            "--auto-approve",
            budget_flag,
            budget,
            "--output-dir",
            str(output_dir),
        ],
    )

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(output_dir)
    assert export.outcome == outcome
    assert export.stop_reason == "interrupted"
    assert export.usage is not None and export.usage.total_tokens > 10
    assert export.cost_usd is not None and export.cost_usd > 10
    assert len(streaming_mock_server.requests) >= 2


def test_missing_prompt_file_is_a_usage_error(
    e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A prompt file path that does not exist.
    *Do*: Run `vibe` with it.
    *Assert*: Exit 1 and the export reports a usage error.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        [
            "--prompt-file",
            str(tmp_path / "missing.md"),
            "--output-dir",
            str(output_dir),
        ],
    )

    # Assert
    assert result.returncode == 1
    export = read_export(output_dir)
    assert export.outcome == "usage_error"
    assert export.exit_code == 1
    assert export.error is not None and "missing.md" in export.error.message
    assert export.vibe_version == __version__


def test_missing_workdir_is_a_usage_error(tmp_path: Path) -> None:
    """*Prepare*: A --workdir that does not exist.
    *Do*: Run `vibe -p` with it and an output directory.
    *Assert*: Exit 1 and the export reports a usage error naming the workdir.
    """
    # Prepare
    output_dir = tmp_path / "out"
    missing = tmp_path / "missing-workdir"

    # Do
    result = run_vibe_headless(
        missing, ["-p", "Hello", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 1
    export = read_export(output_dir)
    assert export.outcome == "usage_error"
    assert export.error is not None
    assert "missing-workdir" in export.error.message
    assert export.session_id is None


def test_invalid_config_is_a_config_error(e2e_workdir: Path, tmp_path: Path) -> None:
    """*Prepare*: A config file that does not parse.
    *Do*: Run `vibe -p`.
    *Assert*: Exit 1 and the export reports a config error with no config.
    """
    # Prepare
    config_path = Path(os.environ["VIBE_HOME"]) / "config.toml"
    config_path.write_text("active_model = [\n", encoding="utf-8")
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir, ["-p", "hello", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 1
    export = read_export(output_dir)
    assert export.outcome == "config_error"
    assert export.config is None


_TOOL_PID_FILE = "tool.pid"
# How long a stopped run may take to exit: the stop grace period plus process
# startup and teardown on a loaded CI machine.
_EXIT_BUDGET_S = 20.0


def _long_tool_call_then_text(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    if request_index > 0:
        return assistant_text_chunks("All done.")
    return single_tool_call_chunks(
        call_id="call_long",
        tool_name="bash",
        arguments={"command": f"echo $$ > {_TOOL_PID_FILE}; exec sleep 300"},
    )


def _wait_for_tool_pid(workdir: Path, deadline_s: float = 30.0) -> int:
    pid_file = workdir / _TOOL_PID_FILE
    end = time.monotonic() + deadline_s
    while not (text := _read_text(pid_file)).strip():
        if time.monotonic() >= end:
            raise AssertionError("the tool never started")
        time.sleep(0.05)
    return int(text)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _process_gone(pid: int, wait_s: float = 5.0) -> bool:
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _assert_resumable(workdir: Path, session_id: str, tmp_path: Path) -> None:
    result = run_vibe_headless(
        workdir,
        [
            "-p",
            "Carry on",
            "--resume",
            session_id,
            "--output-dir",
            str(tmp_path / "resumed"),
        ],
    )
    assert result.returncode == 0, result.stderr
    assert "All done." in result.stdout
    assert read_export(tmp_path / "resumed").session_id == session_id


@pytest.mark.parametrize(
    "streaming_mock_server", [_long_tool_call_then_text], indirect=True
)
def test_time_limit_stops_the_turn_and_its_tool_and_exits_three(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model whose first answer runs a tool that sleeps for minutes.
    *Do*: Run `vibe -p` with a time limit long enough for the tool to start.
    *Assert*: Exit 3 soon after the deadline, outcome `deadline`, the tool process
    is gone, and the session resumes.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    started = time.monotonic()
    result = run_vibe_headless(
        e2e_workdir,
        [
            "-p",
            "Run the long job",
            "--auto-approve",
            "--time-limit",
            str(_TIME_LIMIT_S),
            "--output-dir",
            str(output_dir),
        ],
    )
    elapsed = time.monotonic() - started

    # Assert
    assert result.returncode == 3, result.stderr
    assert elapsed < _TIME_LIMIT_S + _EXIT_BUDGET_S
    export = read_export(output_dir)
    assert export.outcome == "deadline"
    assert export.exit_code == 3
    assert export.limits.time_limit_s == _TIME_LIMIT_S
    assert _process_gone(_wait_for_tool_pid(e2e_workdir, deadline_s=0))
    assert len(streaming_mock_server.requests) == 1
    assert export.session_id is not None
    _assert_resumable(e2e_workdir, export.session_id, tmp_path)


@pytest.mark.parametrize(
    "streaming_mock_server", [_long_tool_call_then_text], indirect=True
)
@pytest.mark.parametrize(
    "stop_signal", [signal.SIGTERM, signal.SIGINT], ids=["sigterm", "ctrl-c"]
)
def test_stop_signal_stops_the_turn_and_its_tool_and_exits_three(
    streaming_mock_server: StreamingMockServer,
    e2e_workdir: Path,
    tmp_path: Path,
    stop_signal: signal.Signals,
) -> None:
    """*Prepare*: A run whose tool is sleeping for minutes, in its own process
    group as a terminal's foreground job is.
    *Do*: Send SIGTERM, or SIGINT as Ctrl-C does, to that process group.
    *Assert*: Exit 3 within the grace period, outcome `terminated`, the tool
    process is gone, and the session resumes.
    """
    # Prepare
    output_dir = tmp_path / "out"
    process = subprocess.Popen(
        [
            VIBE_EXECUTABLE,
            "--workdir",
            str(e2e_workdir),
            "-p",
            "Run the long job",
            "--auto-approve",
            "--output-dir",
            str(output_dir),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=os.environ.copy(),
        start_new_session=True,
    )
    try:
        tool_pid = _wait_for_tool_pid(e2e_workdir)

        # Do
        started = time.monotonic()
        os.killpg(process.pid, stop_signal)
        _, stderr = process.communicate(timeout=_EXIT_BUDGET_S)
        elapsed = time.monotonic() - started
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    # Assert
    assert process.returncode == 3, stderr
    assert elapsed < _EXIT_BUDGET_S
    export = read_export(output_dir)
    assert export.outcome == "terminated"
    assert export.exit_code == 3
    assert _process_gone(tool_pid)
    assert len(streaming_mock_server.requests) == 1
    assert export.session_id is not None
    _assert_resumable(e2e_workdir, export.session_id, tmp_path)


@pytest.mark.parametrize("streaming_mock_server", [_text_reply], indirect=True)
@pytest.mark.parametrize("backend", ["generic", "mistral"])
def test_model_request_carries_the_configured_top_p_and_output_cap(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, backend: str
) -> None:
    """*Prepare*: A model configured with `top_p` and `max_output_tokens`, behind
    either backend adapter.
    *Do*: Run `vibe -p`.
    *Assert*: The model request carries both values.
    """
    # Prepare
    write_e2e_config(
        Path(os.environ["VIBE_HOME"]),
        streaming_mock_server.api_base,
        backend=backend,
        model_settings=["top_p = 0.9", "max_output_tokens = 123"],
    )

    # Do
    result = run_vibe_headless(e2e_workdir, ["-p", "hello"])

    # Assert
    assert result.returncode == 0, result.stderr
    request: dict[str, Any] = dict(streaming_mock_server.requests[0])
    assert request["top_p"] == 0.9
    assert request["max_tokens"] == 123


def _overloaded_once(
    request_index: int, _payload: ChatCompletionsRequestPayload
) -> int:
    return 520 if request_index == 0 else 200


def test_mistral_backend_retries_http_520_and_finishes(
    e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A Mistral-backend endpoint that answers HTTP 520 once, then replies.
    *Do*: Run `vibe -p`.
    *Assert*: Exit 0 after a second request.
    """
    # Prepare
    server = StreamingMockServer(
        chunk_factory=_text_reply, status_factory=_overloaded_once
    )
    server.start()
    write_e2e_config(Path(os.environ["VIBE_HOME"]), server.api_base, backend="mistral")
    output_dir = tmp_path / "out"

    # Do
    try:
        result = run_vibe_headless(
            e2e_workdir, ["-p", "hello", "--output-dir", str(output_dir)]
        )
    finally:
        server.stop()

    # Assert
    assert result.returncode == 0, result.stderr
    assert "All done." in result.stdout
    assert read_export(output_dir).outcome == "finished"
    assert len(server.requests) == 2


def _truncated_reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=1,
            delta={"role": "assistant", "content": "The answer is cut"},
            finish_reason=None,
        ),
        StreamingMockServer.build_chunk(
            created=2,
            delta={},
            finish_reason="length",
            usage={"prompt_tokens": 3, "completion_tokens": 4},
        ),
    ]


@pytest.mark.parametrize("streaming_mock_server", [_truncated_reply], indirect=True)
def test_response_cut_off_at_the_output_cap_is_a_length_outcome(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model whose answer stops at the output cap.
    *Do*: Run `vibe -p`.
    *Assert*: Exit 3 and the export reports `length`.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir, ["-p", "hello", "--output-dir", str(output_dir)]
    )

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "length"
    assert export.exit_code == 3
    assert export.stop_reason == "length"
    assert len(streaming_mock_server.requests) == 1


def _refused_reply(
    _request_index: int, _payload: ChatCompletionsRequestPayload
) -> list[dict[str, object]]:
    return [
        StreamingMockServer.build_chunk(
            created=1, delta={"role": "assistant", "content": ""}, finish_reason=None
        ),
        StreamingMockServer.build_chunk(
            created=2,
            delta={},
            finish_reason="refusal",
            usage={"prompt_tokens": 3, "completion_tokens": 1},
        ),
    ]


@pytest.mark.parametrize("streaming_mock_server", [_refused_reply], indirect=True)
def test_a_model_refusal_is_a_refusal_outcome(
    streaming_mock_server: StreamingMockServer, e2e_workdir: Path, tmp_path: Path
) -> None:
    """*Prepare*: A model that refuses to answer. Only the legacy runtime
    reports a refusal as one, so the run uses it.
    *Do*: Run `vibe -p`.
    *Assert*: Exit 3, and the export reports `refusal` with the error's code.
    """
    # Prepare
    output_dir = tmp_path / "out"

    # Do
    result = run_vibe_headless(
        e2e_workdir,
        ["--legacy-harness", "-p", "hello", "--output-dir", str(output_dir)],
    )

    # Assert
    assert result.returncode == 3, result.stderr
    export = read_export(output_dir)
    assert export.outcome == "refusal"
    assert export.exit_code == 3
    assert export.error is not None
    assert export.error.code == "refusal"
    assert "declined to respond" in export.error.message
    assert len(streaming_mock_server.requests) == 1
