#!/usr/bin/env python3

from __future__ import annotations

import argparse
import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


async def terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
            await proc.wait()


async def send_message(
    proc: asyncio.subprocess.Process, message: dict[str, object]
) -> None:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(message).encode() + b"\n")
    await proc.stdin.drain()


async def read_response(
    proc: asyncio.subprocess.Process, request_id: str
) -> dict[str, object]:
    assert proc.stdout is not None
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        if not line:
            raise RuntimeError(f"{request_id} returned no response")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("id") != request_id:
            continue
        if "error" in response:
            raise RuntimeError(f"{request_id} returned an error: {response['error']}")
        return response


async def smoke_binary(binary: Path, *, experimental_harness: bool) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["VIBE_HOME"] = str(Path(tmp) / ".vibe")
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env["MISTRAL_API_KEY"] = "smoke-test"
        arguments = ["--experimental-harness"] if experimental_harness else []
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            *arguments,
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
        )
        failure: str | None = None
        try:
            await send_message(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": "initialize",
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "smoke-test",
                            "version": "0.0.0",
                            "entrypoint": "programmatic",
                        },
                        "capabilities": {},
                    },
                },
            )
            response = await read_response(proc, "initialize")
            result = response.get("result")
            if not isinstance(result, dict):
                failure = f"unexpected initialize response: {response}"
            else:
                server_info = result.get("serverInfo", {})
                if server_info.get("name") != "vibe-app-server":
                    failure = f"unexpected server info: {server_info}"
                else:
                    print("PASS: app-server initialize")

            if failure is None and experimental_harness:
                await send_message(
                    proc, {"jsonrpc": "2.0", "method": "initialized", "params": {}}
                )
                await send_message(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": "session-start",
                        "method": "session/start",
                        "params": {"agentConfig": {"cwd": tmp}},
                    },
                )
                response = await read_response(proc, "session-start")
                result = response.get("result")
                state = result.get("state") if isinstance(result, dict) else None
                session = state.get("session") if isinstance(state, dict) else None
                session_id = session.get("id") if isinstance(session, dict) else None
                if not isinstance(session_id, str) or not session_id:
                    failure = f"unexpected session/start response: {response}"
                else:
                    print("PASS: experimental Harness session/start")
        except (TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            failure = f"binary smoke test failed: {error}"
        finally:
            await terminate(proc)
        if failure is not None:
            assert proc.stderr is not None
            stderr = (await proc.stderr.read()).decode(errors="replace")
            fail(f"{failure}\nstderr: {stderr}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary_dir", type=Path)
    parser.add_argument("--experimental-harness", action="store_true")
    args = parser.parse_args()

    binary_dir = args.binary_dir
    binary_name = (
        "vibe-app-server.exe" if platform.system() == "Windows" else "vibe-app-server"
    )
    binary = binary_dir / binary_name
    if not binary.exists():
        fail(f"binary not found at {binary}")
    if platform.system() != "Windows":
        binary.chmod(0o755)

    asyncio.run(smoke_binary(binary, experimental_harness=args.experimental_harness))


if __name__ == "__main__":
    main()
