"""A client for `vibe run-server`, driving the Rust CLI's process over its stdio."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from typing import Any

from tests.e2e.common import RUST_CLI_ENV

PROTOCOL_VERSION = 1


class RunServerClient:
    def __init__(self, process: subprocess.Popen[str]) -> None:
        assert process.stdin is not None and process.stdout is not None
        self.process = process
        self.stdout_lines: list[str] = []
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._pending: list[dict[str, Any]] = []
        self._stderr: list[str] = []
        self._next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        self._stderr.extend(self.process.stderr)

    def _next(self, timeout: float) -> dict[str, Any]:
        line = self._lines.get(timeout=timeout)
        assert line is not None, "the run server closed its output"
        self.stdout_lines.append(line)
        return json.loads(line)

    def send(self, message: Mapping[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(
        self, method: str, params: Mapping[str, Any], timeout: float = 30
    ) -> dict[str, Any]:
        self._next_id += 1
        request_id = self._next_id
        self.send({
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        })
        while True:
            message = self._next(timeout)
            if message.get("id") == request_id:
                return message
            self._pending.append(message)

    def initialize(self) -> dict[str, Any]:
        reply = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientInfo": {"name": "test", "version": "0"},
            },
        )
        return reply["result"]

    def start_run(self, run_id: str, socket: Path, **params: Any) -> dict[str, Any]:
        return self.request(
            "run/start",
            {
                "runId": run_id,
                "prompt": params.pop("prompt", "Look up harness"),
                "agentSocket": str(socket),
                "autoApprove": True,
                **params,
            },
        )

    def ended(self, run_id: str, timeout: float = 60) -> dict[str, Any]:
        """The run's `run/ended` params."""
        while True:
            for message in self._pending:
                if (
                    message.get("method") == "run/ended"
                    and message["params"]["runId"] == run_id
                ):
                    self._pending.remove(message)
                    return message["params"]
            self._pending.append(self._next(timeout))

    def close_input(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.close()

    def finish(self, timeout: float = 60) -> tuple[int, str]:
        """Close the input, and return the exit code and stderr; reads the rest."""
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.close_input()
        code = self.process.wait(timeout=timeout)
        while (line := self._lines.get(timeout=5)) is not None:
            self.stdout_lines.append(line)
            self._pending.append(json.loads(line))
        self._stderr_reader.join(timeout=5)
        return code, "".join(self._stderr)

    def app_servers(self) -> int:
        """The app-server processes the run server has running."""
        return len(self.app_server_pids())

    def app_server_pids(self) -> list[int]:
        children = subprocess.run(
            ["pgrep", "-P", str(self.process.pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        return [int(pid) for pid in children.stdout.split()]


@contextmanager
def run_server(
    cwd: Path, *args: str, env: Mapping[str, str] | None = None
) -> Iterator[RunServerClient]:
    """`vibe run-server` in `cwd`, on this checkout's app server."""
    environment = dict(os.environ if env is None else env)
    executable = environment.pop(RUST_CLI_ENV)
    environment["VIBE_APP_SERVER_BIN"] = str(
        Path(sys.executable).with_name("vibe-app-server")
    )
    for name in ("VIBE_APP_SERVER_CMD", "VIBE_APP_SERVER_CWD", "VIBE_CLI"):
        environment.pop(name, None)
    process = subprocess.Popen(
        [executable, "run-server", *args],
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        yield RunServerClient(process)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
