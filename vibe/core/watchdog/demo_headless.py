from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from types import TracebackType
from typing import Any, cast

from vibe.core.paths._vibe_home import VIBE_HOME
from vibe.core.watchdog.demo_report import DemoRunReport, DemoTraceEntry
from vibe.core.watchdog.models import IncidentState, ObserverState, RunState
from vibe.core.watchdog.paths import WatchdogPaths
from vibe.core.watchdog.reducer import apply_event
from vibe.core.watchdog.store import WatchdogStore

HEADLESS_DEMO_PROMPT = (
    "Run the Watchcat integration canary. Reproduce the repeated todo read failure "
    "and follow the injected recovery context with a changed todo write action."
)
REPEATED_READ_COUNT = 4
RECOVERY_RESPONSE_INDEX = REPEATED_READ_COUNT + 1
TOTAL_MODEL_RESPONSES = RECOVERY_RESPONSE_INDEX + 1
DEFAULT_RESPONSE_DELAY = 0.3


class _FixtureServer:
    def __init__(
        self, *, response_delay: float, progress: Callable[[int, int, str], None] | None
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self._response_delay = response_delay
        self._progress = progress
        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self) -> _FixtureServer:
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=1)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                del format, args

            def do_POST(self) -> None:
                if self.path != "/v1/chat/completions":
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                payload = cast(
                    dict[str, Any], json.loads(self.rfile.read(length).decode("utf-8"))
                )
                with fixture._lock:
                    fixture.requests.append(payload)
                    index = len(fixture.requests)
                if fixture._progress is not None:
                    fixture._progress(
                        index, TOTAL_MODEL_RESPONSES, _response_label(index)
                    )
                time.sleep(fixture._response_delay)
                response = _completion(index)
                body = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler


def _completion(index: int) -> dict[str, Any]:
    if index <= REPEATED_READ_COUNT:
        message = _tool_message(f"headless-read-{index}", {"action": "read"})
        finish_reason = "tool_calls"
    elif index == RECOVERY_RESPONSE_INDEX:
        message = _tool_message(
            "headless-write-1",
            {
                "action": "write",
                "todos": [
                    {
                        "id": "mitigated",
                        "content": "Changed action after Watchcat recovery",
                        "status": "completed",
                        "priority": "high",
                    }
                ],
            },
        )
        finish_reason = "tool_calls"
    else:
        message = {"role": "assistant", "content": "Headless canary complete."}
        finish_reason = "stop"
    return {
        "id": f"watchcat-fixture-{index}",
        "object": "chat.completion",
        "created": index,
        "model": "watchcat-fixture",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


def _response_label(index: int) -> str:
    if index <= REPEATED_READ_COUNT:
        return f"controlled repeated read {index}/{REPEATED_READ_COUNT}"
    if index == RECOVERY_RESPONSE_INDEX:
        return "recovery received; changing read -> write"
    return "verification complete; closing run"


def _tool_message(call_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "Executing controlled failure step.",
        "tool_calls": [
            {
                "id": call_id,
                "index": 0,
                "type": "function",
                "function": {
                    "name": "todo",
                    "arguments": json.dumps(arguments, separators=(",", ":")),
                },
            }
        ],
    }


def _write_config(vibe_home: Path, api_base: str) -> None:
    vibe_home.mkdir(parents=True, exist_ok=True)
    (vibe_home / "config.toml").write_text(
        "\n".join([
            'active_model = "watchcat-fixture"',
            'default_agent = "auto-approve"',
            'enabled_tools = ["todo"]',
            "enable_connectors = false",
            "enable_update_checks = false",
            "enable_telemetry = false",
            "include_project_context = false",
            "include_prompt_detail = false",
            "",
            "[[providers]]",
            'name = "watchcat-fixture"',
            f'api_base = "{api_base}"',
            'api_key_env_var = "WATCHCAT_DEMO_API_KEY"',
            'backend = "generic"',
            "",
            "[[models]]",
            'name = "watchcat-fixture"',
            'provider = "watchcat-fixture"',
            'alias = "watchcat-fixture"',
        ]),
        encoding="utf-8",
    )


def run_headless_demo(
    workdir: Path | None = None,
    *,
    response_delay: float = DEFAULT_RESPONSE_DELAY,
    progress: Callable[[int, int, str], None] | None = None,
) -> DemoRunReport:
    if response_delay < 0:
        raise ValueError("response_delay must be non-negative")
    executable = shutil.which("vibe")
    if executable is None:
        raise RuntimeError("`vibe` executable not found on PATH")
    launch_cwd = (workdir or Path.cwd()).resolve()
    with (
        _FixtureServer(response_delay=response_delay, progress=progress) as fixture,
        _temporary_demo_dir() as temporary,
    ):
        temporary_home = temporary / "home"
        demo_workdir = temporary / "workdir"
        demo_workdir.mkdir()
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=demo_workdir,
            capture_output=True,
            check=True,
        )
        _write_config(temporary_home, fixture.api_base)
        env = {
            **os.environ,
            "VIBE_HOME": str(temporary_home),
            "WATCHCAT_DEMO_API_KEY": "local-fixture",
        }
        command = [
            executable,
            "-p",
            HEADLESS_DEMO_PROMPT,
            "--watchcat",
            "--auto-approve",
            "--trust",
            "--workdir",
            str(demo_workdir),
        ]
        result = subprocess.run(
            command,
            cwd=launch_cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout)[-2_000:]
            raise RuntimeError(f"headless Vibe exited {result.returncode}: {detail}")
        match = re.search(r"WATCHCAT run=([^ ]+) artifacts=([^\n\r]+)", result.stderr)
        if match is None:
            raise RuntimeError("headless Vibe did not report Watchcat artifacts")
        run_id, source_text = match.groups()
        source = Path(source_text.strip())
        destination = VIBE_HOME.path / "watchcat" / "headless-runs" / run_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, dirs_exist_ok=True)
        requests = list(fixture.requests)
    return _build_report(run_id, destination, requests, command)


def _build_report(
    run_id: str, run_dir: Path, requests: list[dict[str, Any]], command: list[str]
) -> DemoRunReport:
    paths = WatchdogPaths(run_dir=run_dir)
    events = _run_async(WatchdogStore(paths).load_events())
    state = RunState.new(run_id=run_id, session_id=events[0].session_id)
    trace: list[DemoTraceEntry] = []
    for event in events:
        state = apply_event(state, event)
        trace.append(
            DemoTraceEntry(
                sequence=event.sequence,
                event=event.kind.value,
                phase=state.phase.value,
                signal_quality=(
                    "healthy"
                    if state.observer_state == ObserverState.TRUSTED
                    else "degraded"
                ),
                incident_state=state.incident.state.value if state.incident else "-",
            )
        )
    if state.incident is None or state.incident.state != IncidentState.CLOSED:
        actual = state.incident.state.value if state.incident else "none"
        raise RuntimeError(f"headless Vibe expected closed incident, got {actual}")
    recovery_prompts = [
        content
        for request in requests
        for message in request.get("messages", [])
        if isinstance(message, dict)
        if (content := " ".join(_flatten_strings(message.get("content"))))
        if "WATCHCAT RECOVERY HANDOFF" in content
    ]
    if not recovery_prompts:
        observed = [
            " | ".join(_flatten_strings(message.get("content")))[:160]
            for request in requests
            for message in request.get("messages", [])
            if isinstance(message, dict) and message.get("role") == "user"
        ]
        raise RuntimeError(
            "headless Vibe did not receive the recovery prompt; "
            f"observed user messages={observed[-6:]}"
        )
    return DemoRunReport(
        name="headless-cli",
        title="Real Vibe CLI -> programmatic AgentLoop -> verified recovery",
        prompt=HEADLESS_DEMO_PROMPT,
        recovery_prompt=recovery_prompts[-1].replace("\n", " | ")[:240],
        classification="mitigated",
        detector=state.incident.owner or "repeated_call",
        issue="headless_programmatic_repeated_tool_call",
        trigger="4 identical todo reads through the real Vibe executable",
        evidence=[f"fixture_requests={len(requests)}", f"command={command[0]} -p ..."],
        flow=["CLI", "programmatic", "AgentLoop", "tool x4", "recover", "verify"],
        mitigation=["inject recovery prompt", "todo read -> write", "verify"],
        outcome="recovered",
        signal_quality="healthy",
        incident_state=state.incident.state.value,
        llm_scores=[],
        injection_attempts=1,
        context_injections=1,
        artifacts=str(run_dir),
        trace=trace,
    )


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@contextmanager
def _temporary_demo_dir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="vibe-watchcat-headless-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _flatten_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _flatten_strings(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _flatten_strings(item)]
    return []
