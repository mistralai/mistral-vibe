"""Adapt an app-server scenario to the generic PTY harness."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tempfile

from e2e.app_server.config import (
    BATCH_KEYS,
    COLUMNS,
    IDLE_MARKER,
    MINIMAL_HOME_CONFIG,
    REPLAY_BIN,
    REPLAY_NOW_MS,
    ROWS,
    VIBE_DIR,
)
from e2e.app_server.replay import replay_fixture
from e2e.app_server.scenario import Action, AppServerEvent, Request, Scenario
from e2e.pty.capture import CaptureSpec, Launcher, capture, exec_launch
from e2e.pty.screen import Snapshot

# Both clients probe the terminal background (OSC 11) and fall back to the OS
# appearance, so a scenario that renders before the config lands would follow
# the host. Answer dark by default; a scenario overrides it by query string.
_DEFAULT_TERMINAL_RESPONSES = {"\x1b]11;?\x07": "\x1b]11;rgb:0000/0000/0000\x1b\\"}

_PARITY_METHODS = frozenset({
    "callback/result",
    "config/write",
    "connector_catalog/toggle",
    "feedback/record",
    "feedback/shouldShow",
    "mcp_catalog/add",
    "mcp_catalog/login",
    "mcp_catalog/logout",
    "mcp_catalog/toggle",
    "session/agent/update",
    "session/compact",
    "session/shellCommand",
    "session/turn/enqueue",
    "session/turn/queue/remove",
    "session/turn/queue/replace",
    "session/turn/queue/steer",
    "turn/interrupt",
    "turn/start",
    "turn/steer",
    "workspace/trust/decision",
})


@dataclass(frozen=True)
class Capture:
    snapshots: list[Snapshot]
    actions: list[Action]
    requests: list[Request]


@dataclass(frozen=True)
class HeadlessCapture:
    stdout_before_completion: list[Mapping[str, object]]
    stdout: list[Mapping[str, object]]
    stderr: str
    returncode: int
    requests: list[Request]


def capture_scenario(
    command: tuple[str, ...], scenario: Scenario, launch: Launcher = exec_launch
) -> Capture:
    """Capture one Vibe scenario with one client command."""
    with _isolated_home() as home, replay_fixture(scenario) as fixture:
        step_dir, step_fifo, step_fd = (
            _step_fifo() if _has_releases(scenario) else (None, None, None)
        )
        action_log = Path(home, "actions.jsonl")
        request_log = Path(home, "requests.jsonl")
        env = _environment(home, fixture, scenario.env, action_log, request_log)
        if step_fifo is not None:
            env["VIBE_REPLAY_STEP_FIFO"] = step_fifo
        spec = CaptureSpec(
            command=command + scenario.client_args,
            cwd=os.fspath(VIBE_DIR),
            env=env,
            rows=ROWS,
            columns=COLUMNS,
            idle_marker=IDLE_MARKER,
            steps=tuple(step.text for step in scenario.steps),
            resizes=tuple(step.resize for step in scenario.steps),
            batch_keys=None if scenario.settle_per_key else BATCH_KEYS,
            exit_after_last_step=scenario.exit_after_last_step,
            terminal_responses=tuple(
                (query.encode(), response.encode())
                for query, response in {
                    **_DEFAULT_TERMINAL_RESPONSES,
                    **scenario.terminal_responses,
                }.items()
            ),
        )
        try:
            snapshots = capture(
                spec,
                releases=tuple(step.release for step in scenario.steps),
                capture_startup=scenario.capture_startup,
                capture_steps=scenario.capture_steps,
                step_fd=step_fd,
                launch=launch,
            )
            return Capture(
                snapshots,
                _read_actions(action_log),
                _read_requests(request_log, scenario.request_methods),
            )
        finally:
            if step_fd is not None:
                os.close(step_fd)
            if step_fifo is not None:
                os.unlink(step_fifo)
            if step_dir is not None:
                shutil.rmtree(step_dir, ignore_errors=True)


def _headless_events(scenario: Scenario) -> list[AppServerEvent]:
    """Return the single event batch, requiring a final turn/completed."""
    batches = scenario.event_batches()
    if len(batches) != 1 or not batches[0]:
        raise ValueError("headless scenarios require exactly one non-empty event batch")
    events = batches[0]
    if events[-1].get("method") != "turn/completed":
        raise ValueError("headless scenario must end with turn/completed")
    return events


def capture_headless_scenario(
    command: tuple[str, ...], scenario: Scenario
) -> HeadlessCapture:
    """Capture JSONL before releasing the scenario's final `turn/completed`."""
    events = _headless_events(scenario)

    with _isolated_home() as home, replay_fixture(scenario) as fixture:
        step_dir, step_fifo, step_fd = _step_fifo()
        action_log = Path(home, "actions.jsonl")
        request_log = Path(home, "requests.jsonl")
        env = _environment(home, fixture, scenario.env, action_log, request_log)
        env["VIBE_REPLAY_STEP_FIFO"] = step_fifo
        process = subprocess.Popen(
            command + scenario.client_args,
            cwd=VIBE_DIR,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            os.write(step_fd, b"\0" * (len(events) - 1))
            if process.stdout is None:
                raise RuntimeError("headless client stdout is unavailable")
            ready, _, _ = select.select([process.stdout], [], [], 5.0)
            if not ready:
                raise TimeoutError(
                    "headless client emitted no output before turn/completed"
                )
            first_line = process.stdout.readline()
            if process.poll() is not None:
                raise RuntimeError("headless client exited before turn/completed")
            os.write(step_fd, b"\0")
            stdout_tail, stderr = process.communicate(timeout=10)
            before_completion = [json.loads(first_line)]
            stdout = before_completion + [
                json.loads(line) for line in stdout_tail.splitlines() if line
            ]
            return HeadlessCapture(
                stdout_before_completion=before_completion,
                stdout=stdout,
                stderr=stderr,
                returncode=process.returncode,
                requests=_read_logged_requests(request_log),
            )
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(step_fd)
            os.unlink(step_fifo)
            shutil.rmtree(step_dir, ignore_errors=True)


def _has_releases(scenario: Scenario) -> bool:
    return any(step.release for step in scenario.steps)


def _step_fifo() -> tuple[str, str, int]:
    directory = tempfile.mkdtemp(prefix="e2e_replay_step_")
    path = os.path.join(directory, "events")
    os.mkfifo(path)
    return directory, path, os.open(path, os.O_RDWR | os.O_NONBLOCK)


@contextmanager
def _isolated_home() -> Iterator[str]:
    home = tempfile.mkdtemp(prefix="e2e_home_")
    Path(home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
    try:
        yield home
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _environment(
    home: str,
    fixture: str,
    overrides: Mapping[str, str],
    action_log: Path,
    request_log: Path,
) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "TERM": "xterm-256color",
        # Pin truecolor so Textual never downsamples RGB themes to the 256-color
        # cube (ratatui keeps truecolor); the host's COLORTERM must not decide parity.
        "COLORTERM": "truecolor",
        # Pin a non-VS Code terminal so the host's TERM_PROGRAM never decides the
        # promo banner; a scenario overrides it to test the VS Code family.
        "TERM_PROGRAM": "xterm-ghostty",
        "COLUMNS": str(COLUMNS),
        "LINES": str(ROWS),
        "VIBE_HOME": home,
        "MISTRAL_API_KEY": "fake-key",
        "VIBE_TEST_DISABLE_KEYRING": "1",
        "FORCE_COLOR": "1",
        "VIBE_THEME": "ansi-dark",
        "VIBE_REPLAY_FIXTURE": fixture,
        "VIBE_REPLAY_BIN": os.fspath(REPLAY_BIN),
        "VIBE_REPLAY_NOW_MS": str(REPLAY_NOW_MS),
        "VIBE_E2E_ACTION_LOG": os.fspath(action_log),
        "VIBE_REPLAY_REQUEST_LOG": os.fspath(request_log),
        **overrides,
        # Keep clipboard assertions inside the PTY instead of mutating the host.
        "SSH_TTY": "/dev/pts/0",
    })
    environment.pop("NO_COLOR", None)
    return environment


def _read_actions(path: Path) -> list[Action]:
    if not path.exists():
        return []
    return [Action(**json.loads(line)) for line in path.read_text().splitlines()]


def _read_requests(path: Path, extra_methods: frozenset[str]) -> list[Request]:
    """Return RPCs whose wire contract must match between clients."""
    methods = _PARITY_METHODS | extra_methods
    return [
        request
        for request in _read_logged_requests(path)
        if _parity_request(request, methods)
    ]


def _read_logged_requests(path: Path) -> list[Request]:
    """Return every logged client-to-server request, unfiltered."""
    if not path.exists():
        return []
    return [
        Request(message["method"], message.get("params", {}))
        for line in path.read_text().splitlines()
        if (message := json.loads(line)).get("method")
    ]


def _parity_request(request: Request, methods: frozenset[str]) -> bool:
    if request.method in methods:
        return True
    if request.method != "telemetry/record":
        return False
    if request.params.get("name") == "vibe.user_rating_feedback":
        return True
    return (
        request.params.get("name") == "vibe.slash_command_used"
        and request.params.get("properties", {}).get("command_type") == "skill"
    )
