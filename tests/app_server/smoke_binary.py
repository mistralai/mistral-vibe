#!/usr/bin/env python3

from __future__ import annotations

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


async def smoke_initialize(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["VIBE_HOME"] = str(Path(tmp) / ".vibe")
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
        )
        failure: str | None = None
        try:
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(
                json.dumps({
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
                }).encode()
                + b"\n"
            )
            await proc.stdin.drain()
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            if not line:
                failure = "initialize returned no response"
            else:
                response = json.loads(line)
                server_info = response.get("result", {}).get("serverInfo", {})
                if server_info.get("name") != "vibe-app-server":
                    failure = f"unexpected server info: {server_info}"
                else:
                    print("PASS: app-server initialize")
        except (TimeoutError, json.JSONDecodeError) as error:
            failure = f"initialize failed: {error}"
        finally:
            await terminate(proc)
        if failure is not None:
            assert proc.stderr is not None
            stderr = (await proc.stderr.read()).decode(errors="replace")
            fail(f"{failure}\nstderr: {stderr}")


def main() -> None:
    if len(sys.argv) != 2:
        fail(f"Usage: {sys.argv[0]} <binary-dir>")

    binary_dir = Path(sys.argv[1])
    binary_name = (
        "vibe-app-server.exe" if platform.system() == "Windows" else "vibe-app-server"
    )
    binary = binary_dir / binary_name
    if not binary.exists():
        fail(f"binary not found at {binary}")
    if platform.system() != "Windows":
        binary.chmod(0o755)

    asyncio.run(smoke_initialize(binary))


if __name__ == "__main__":
    main()
