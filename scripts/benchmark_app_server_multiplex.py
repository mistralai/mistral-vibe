"""Paired real app-server workload; run in vibe with the editable harness.

uv run --with-editable ../agents/harness/harness/runtimes/python python scripts/benchmark_app_server_multiplex.py
Uses only localhost fixture responses. Requires macOS or Linux (ps).
RSS covers app-server process trees, excluding this driver and Electron.
Each turn streams 32 chunks at 20 ms intervals (2 ms with --saturated).
--history N runs N untimed turns in each new session before it is measured,
since per-update cost grows with the length of a session's history.
The direct-process baseline includes an initialized catalogue and spare process;
it measures interpreter sharing, not Desktop's prewarm acquisition latency.
"""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any

CONFIG = """active_model = "fake-model"
include_project_context = false
[experiments]
enable = false
[telemetry]
enable = false
[[providers]]
name = "fake"
api_base = "http://127.0.0.1:PORT/v1"
api_key_env_var = "TEST_FAKE_API_KEY"
api_style = "openai"
[[models]]
name = "fake-model"
provider = "fake"
alias = "fake-model"
thinking = "off"
"""


async def model(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, saturated: bool
) -> None:
    try:
        headers = await reader.readuntil(b"\r\n\r\n")
        length = next(
            int(line.split(b":", 1)[1])
            for line in headers.splitlines()
            if line.lower().startswith(b"content-length:")
        )
        await reader.readexactly(length)
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
        )
        for _ in range(32):
            writer.write(
                (
                    "data: "
                    + json.dumps({
                        "choices": [
                            {
                                "delta": {
                                    "content": "benchmark response "
                                    * (32 if saturated else 2)
                                },
                                "index": 0,
                                "finish_reason": None,
                            }
                        ]
                    })
                    + "\n\n"
                ).encode()
            )
            await writer.drain()
            await asyncio.sleep(0.002 if saturated else 0.02)
        writer.write(
            (
                "data: "
                + json.dumps({
                    "choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 50, "completion_tokens": 512},
                })
                + "\n\ndata: [DONE]\n\n"
            ).encode()
        )
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


class Process:
    def __init__(self, child: asyncio.subprocess.Process, shared: bool) -> None:
        self.child, self.shared = child, shared
        self.pending, self.closed = {}, {}
        self.turns = {}
        self.sequence = 0
        self.reader = asyncio.create_task(self.read())

    async def read(self) -> None:
        assert self.child.stdout is not None
        async for line in self.child.stdout:
            frame = json.loads(line)
            channel, message = (
                (frame["channelId"], frame["message"]) if self.shared else ("1", frame)
            )
            if message is None:
                self.closed[channel].set()
            elif message.get("method") == "turn/completed":
                self.turns.setdefault(
                    (channel, message["params"]["turn"]["id"]), asyncio.Event()
                ).set()
            elif "result" in message or "error" in message:
                future = self.pending.pop((channel, message["id"]))
                if "error" in message:
                    future.set_exception(RuntimeError(str(message["error"])))
                else:
                    future.set_result(message["result"])
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("server exited"))

    async def send(self, channel: str, message: dict[str, Any] | None) -> None:
        assert self.child.stdin is not None
        frame = {"channelId": channel, "message": message} if self.shared else message
        self.child.stdin.write((json.dumps(frame) + "\n").encode())
        await self.child.stdin.drain()

    async def rpc(
        self, channel: str, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[channel, request_id] = future
        await self.send(
            channel,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        return await asyncio.wait_for(future, 60)

    async def initialize(self, channel: str) -> None:
        self.closed[channel] = asyncio.Event()
        await self.rpc(
            channel,
            "initialize",
            {"clientInfo": {"name": "benchmark", "version": "1"}, "capabilities": {}},
        )
        await self.send(
            channel, {"jsonrpc": "2.0", "method": "initialized", "params": {}}
        )

    async def stop(self) -> None:
        assert self.child.stdin is not None
        if self.child.returncode is None:
            self.child.stdin.close()
        try:
            await asyncio.wait_for(self.child.wait(), 20)
        except TimeoutError:
            self.child.kill()
            await self.child.wait()
            raise
        await self.reader
        assert self.child.returncode == 0, self.child.returncode


async def sample(processes: list[Process]) -> dict[str, int | float]:
    child = await asyncio.create_subprocess_exec(
        "ps", "-axo", "pid=,ppid=,rss=,time=", stdout=asyncio.subprocess.PIPE
    )
    data, _ = await child.communicate()
    rows = [line.split() for line in data.decode().splitlines()]
    pids = {p.child.pid for p in processes if p.child.returncode is None}
    while True:
        descendants = {int(pid) for pid, parent, *_ in rows if int(parent) in pids}
        if descendants <= pids:
            break
        pids |= descendants
    selected = [
        (int(rss), elapsed) for pid, _, rss, elapsed in rows if int(pid) in pids
    ]

    def seconds(value: str) -> float:
        result = 0.0
        for field in value.split(":"):
            result = result * 60 + float(field)
        return result

    return {
        "processes": len(pids),
        "rss_mib": round(sum(rss for rss, _ in selected) / 1024, 1),
        "cpu_seconds": round(sum(seconds(t) for _, t in selected), 2),
    }


async def run(  # noqa: PLR0915
    shared: bool, port: int, history: int
) -> None:
    with tempfile.TemporaryDirectory(prefix="vibe-benchmark-") as directory:
        root = Path(directory)
        (root / "config.toml").write_text(CONFIG.replace("PORT", str(port)))
        env = {
            **os.environ,
            "VIBE_HOME": directory,
            "TEST_FAKE_API_KEY": "test",
            "LOG_LEVEL": "ERROR",
        }
        processes, sessions = [], []

        async def spawn() -> Process:
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "from vibe.app_server.stdio import main; main()",
                "--experimental-harness",
                *(["--multiplex"] if shared else []),
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=16 * 1024 * 1024,
            )
            process = Process(child, shared)
            processes.append(process)
            return process

        try:
            # Catalogue and prewarm reflect the dedicated pool's steady state.
            catalogue = await spawn()
            await catalogue.initialize("1")
            if not shared:
                warm = await spawn()
                await warm.initialize("1")
            next_id = 1

            async def create() -> tuple[Process, str, str]:
                nonlocal next_id
                next_id += 1
                channel = str(next_id) if shared else "1"
                process = catalogue if shared else await spawn()
                await process.initialize(channel)
                result = await process.rpc(
                    channel, "session/start", {"agentConfig": {"cwd": directory}}
                )
                return process, channel, result["state"]["session"]["id"]

            async def turn(session: tuple[Process, str, str]) -> float:
                process, channel, session_id = session
                started = time.perf_counter()
                result = await process.rpc(
                    channel,
                    "turn/start",
                    {
                        "sessionId": session_id,
                        "message": [
                            {"type": "text", "text": "Stream the benchmark response."}
                        ],
                    },
                )
                completed = process.turns.setdefault(
                    (channel, result["turn"]["id"]), asyncio.Event()
                )
                await asyncio.wait_for(completed.wait(), 60)
                duration = (time.perf_counter() - started) * 1000
                state = await process.rpc(
                    channel, "session/read", {"sessionId": session_id}
                )
                status = next(
                    t["status"]
                    for t in state["state"]["turns"]
                    if t["id"] == result["turn"]["id"]
                )
                assert status == "completed", state
                return duration

            for target in (1, 5, 10, 20):
                started = time.perf_counter()
                created = await asyncio.gather(
                    *(create() for _ in range(target - len(sessions)))
                )
                created_ms = (time.perf_counter() - started) * 1000
                for _ in range(history):
                    await asyncio.gather(*(turn(s) for s in created))
                sessions.extend(created)
                durations = await asyncio.gather(*(turn(s) for s in sessions))
                await asyncio.sleep(0.25)
                print(
                    json.dumps({
                        "mode": "shared" if shared else "dedicated",
                        "sessions": target,
                        "history_turns": history,
                        "create_batch_ms": round(created_ms),
                        "turn_median_ms": round(statistics.median(durations)),
                        **await sample(processes),
                    }),
                    flush=True,
                )
            if shared:
                for cycle in range(5):
                    for p, ch, sid in sessions:
                        await p.rpc(ch, "session/stop", {"sessionId": sid})
                        await p.send(ch, None)
                        await p.closed[ch].wait()
                    sessions = await asyncio.gather(*(create() for _ in range(20)))
                    await asyncio.gather(*(turn(s) for s in sessions))
                    print(
                        json.dumps({
                            "mode": "shared-churn",
                            "created_sessions": 40 + cycle * 20,
                            **await sample(processes),
                        }),
                        flush=True,
                    )
        finally:
            await asyncio.gather(*(p.stop() for p in processes))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--saturated",
        action="store_true",
        help="Stream large chunks with a 2 ms interval to expose CPU contention.",
    )
    parser.add_argument(
        "--history",
        type=int,
        default=0,
        help="Untimed turns each new session runs before it is measured.",
    )
    args = parser.parse_args()
    print(
        json.dumps({
            "saturated": args.saturated,
            "history": args.history,
            "platform": sys.platform,
        }),
        flush=True,
    )
    server = await asyncio.start_server(
        partial(model, saturated=args.saturated), "127.0.0.1", 0
    )
    async with server:
        for shared in (False, True):
            await run(shared, server.sockets[0].getsockname()[1], args.history)


if __name__ == "__main__":
    asyncio.run(main())
