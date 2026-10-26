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


def shell_probe_command() -> str:
    # Works under the manual shell's POSIX shell, Git Bash, and cmd.
    if platform.system() == "Windows":
        return "echo vibe-smoke"
    return "printf vibe-smoke"


async def smoke_binary(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["VIBE_HOME"] = str(Path(tmp) / ".vibe")
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env["MISTRAL_API_KEY"] = "smoke-test"
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
        )
        failure: str | None = None
        session_id: str = ""
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

            if failure is None:
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
                raw_session_id = (
                    session.get("id") if isinstance(session, dict) else None
                )
                if not isinstance(raw_session_id, str) or not raw_session_id:
                    failure = f"unexpected session/start response: {response}"
                else:
                    session_id = raw_session_id
                    print("PASS: Unified Harness session/start")

            if failure is None:
                assert proc.stdout is not None
                await send_message(
                    proc,
                    {
                        "jsonrpc": "2.0",
                        "id": "shell-command",
                        "method": "session/shellCommand",
                        "params": {
                            "sessionId": session_id,
                            "command": shell_probe_command(),
                            "action": "run",
                            "cwd": tmp,
                        },
                    },
                )
                # The output streams via history/entryUpdated notifications,
                # not the response. The command text is echoed in the
                # entryAdded detail and the terminal state's display, so only
                # an append to /state/outputText proves the shell ran.
                probe_seen = False
                while True:
                    line = await asyncio.wait_for(proc.stdout.readline(), timeout=60)
                    if not line:
                        raise RuntimeError("shell-command returned no response")
                    message = json.loads(line)
                    if (
                        isinstance(message, dict)
                        and message.get("method") == "history/entryUpdated"
                    ):
                        for operation in message.get("params", {}).get("patch", []):
                            if operation.get(
                                "path"
                            ) == "/state/outputText" and "vibe-smoke" in str(
                                operation.get("value")
                            ):
                                probe_seen = True
                    if (
                        isinstance(message, dict)
                        and message.get("id") == "shell-command"
                    ):
                        if "error" in message:
                            raise RuntimeError(
                                f"shell-command returned an error: {message['error']}"
                            )
                        break
                if not probe_seen:
                    failure = "session/shellCommand produced no shell output"
                else:
                    print("PASS: session/shellCommand ran the host shell")
        except (TimeoutError, json.JSONDecodeError, RuntimeError) as error:
            failure = f"binary smoke test failed: {error}"
        finally:
            await terminate(proc)
        if failure is not None:
            assert proc.stderr is not None
            stderr = (await proc.stderr.read()).decode(errors="replace")
            fail(f"{failure}\nstderr: {stderr}")


def require_bundled_winpty(binary_dir: Path) -> None:
    winpty_dir = binary_dir / "_internal" / "winpty"
    has_extension = any(winpty_dir.glob("_winpty*.pyd"))
    has_conpty = (winpty_dir / "conpty.dll").is_file()
    if not (has_extension and has_conpty):
        fail(
            f"winpty is not bundled under {winpty_dir}; the Windows shell would "
            "silently fall back to the non-PTY backend"
        )
    print("PASS: winpty bundled")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary_dir", type=Path)
    args = parser.parse_args()

    binary_dir = args.binary_dir
    binary_name = (
        "vibe-app-server.exe" if platform.system() == "Windows" else "vibe-app-server"
    )
    binary = binary_dir / binary_name
    if not binary.exists():
        fail(f"binary not found at {binary}")
    if platform.system() == "Windows":
        require_bundled_winpty(binary_dir)
    else:
        binary.chmod(0o755)

    asyncio.run(smoke_binary(binary))


if __name__ == "__main__":
    main()
