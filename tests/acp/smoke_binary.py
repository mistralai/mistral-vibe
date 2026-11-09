#!/usr/bin/env python3
"""Smoke tests for the built vibe-acp binary.

Usage: python tests/acp/smoke_binary.py [--quick] <binary-dir>

--quick runs only tests 1-3, which need no pty and no ACP session.

Tests:
  1. --version exits successfully
  2. The Rust TUI `--setup` runs is bundled and starts
  3. --setup-app-server serves the setup RPCs the bundled TUI calls
  4. --setup renders the Rust onboarding wizard on the bundled app-server
  5. ACP initialize returns the expected agent info, and a new session
     offers the bundled built-in plugin skills
  6. (Linux) No ELF binaries require executable stack (GNU_STACK RWE)
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, NoReturn

from acp import (
    PROTOCOL_VERSION,
    Client,
    CreateElicitationResponse,
    RequestError,
    connect_to_agent,
)
from acp.schema import AvailableCommandsUpdate, ClientCapabilities, Implementation


class _SmokeClient(Client):
    def __init__(self) -> None:
        # The first command list a new session announces.
        self.commands: asyncio.Future[list[str]] = (
            asyncio.get_running_loop().create_future()
        )

    def on_connect(self, conn: Any) -> None:
        pass

    async def request_permission(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/request_permission")

    async def write_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/create")

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any
    ) -> CreateElicitationResponse:
        raise RequestError.method_not_found("elicitation/create")

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        raise RequestError.method_not_found("elicitation/complete")

    async def terminal_output(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, *args: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        raise RequestError.method_not_found(method)

    async def session_update(
        self, session_id: str, update: Any, **_kwargs: Any
    ) -> None:
        if isinstance(update, AvailableCommandsUpdate) and not self.commands.done():
            self.commands.set_result([
                command.name for command in update.available_commands
            ])


async def _terminate(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
            await proc.wait()


def _fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def test_version(binary: Path) -> None:
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        _fail(
            f"--version exited with code {result.returncode}\nstderr: {result.stderr}"
        )
    print(f"PASS: --version -> {result.stdout.strip()}")


def test_bundled_rust_tui(binary_dir: Path) -> None:
    name = "vibe-rs.exe" if platform.system() == "Windows" else "vibe-rs"
    tui = next(binary_dir.rglob(f"vibe/_bin/{name}"), None)
    if tui is None:
        _fail(f"vibe/_bin/{name} is missing from the bundle")
    result = subprocess.run(
        [str(tui), "--version"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        _fail(f"bundled vibe-rs --version failed\nstderr: {result.stderr}")
    print(f"PASS: bundled vibe-rs -> {result.stdout.strip()}")


async def _app_server_request(
    proc: asyncio.subprocess.Process, request_id: str, method: str, params: Any
) -> Any:
    assert proc.stdin is not None
    assert proc.stdout is not None
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    proc.stdin.write(json.dumps(message).encode() + b"\n")
    await proc.stdin.drain()
    while True:
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        if not line:
            _fail(f"--setup-app-server closed before answering {method}")
        response = json.loads(line)
        if not isinstance(response, dict) or response.get("id") != request_id:
            continue
        if "error" in response:
            _fail(f"--setup-app-server {method} failed: {response['error']}")
        return response.get("result")


async def test_setup_app_server_mode(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["VIBE_HOME"] = str(Path(tmp) / ".vibe")
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env.pop("MISTRAL_API_KEY", None)
        proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--setup-app-server",
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
            cwd=tmp,
        )
        try:
            client_info = {
                "name": "smoke-test",
                "version": "0.0.0",
                "entrypoint": "acp",
            }
            init = await _app_server_request(
                proc,
                "initialize",
                "initialize",
                {"clientInfo": client_info, "capabilities": {}},
            )
            if init.get("serverInfo", {}).get("name") != "vibe-app-server":
                _fail(f"unexpected --setup-app-server initialize result: {init}")
            assert proc.stdin is not None
            proc.stdin.write(b'{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
            status = await _app_server_request(proc, "status", "setup/status", {})
            if status.get("hasApiKey") is not False:
                _fail(f"unexpected --setup-app-server setup/status result: {status}")
            print("PASS: --setup-app-server answers initialize and setup/status")
        finally:
            await _terminate(proc)


def _screen_text(output: bytearray) -> str:
    text = output.decode("utf-8", errors="replace")
    return re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;?<>]*[ -/]*[@-~]", "", text))


def test_setup_renders_onboarding(binary: Path) -> None:
    if platform.system() == "Windows":
        # Windows does not provide the Unix pty module used to drive the TUI here.
        print("SKIP: --setup pty smoke test (Windows)")
        return

    import fcntl
    import pty
    import select
    import termios

    with tempfile.TemporaryDirectory() as tmp:
        env = os.environ.copy()
        env["VIBE_HOME"] = str(Path(tmp) / "home")
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"
        env["TERM"] = env.get("TERM") or "xterm-256color"
        env.pop("MISTRAL_API_KEY", None)
        master_fd, slave_fd = pty.openpty()
        # A fresh pty is 0x0, where the TUI has nowhere to draw.
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 120, 0, 0))
        proc = subprocess.Popen(
            [str(binary), "--setup"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=tmp,
        )
        os.close(slave_fd)

        output = bytearray()
        # The frozen app-server boot is slow on loaded CI runners.
        deadline = time.monotonic() + 60
        next_enter = None
        try:
            while time.monotonic() < deadline and proc.poll() is None:
                # The welcome screen paints before the app-server boots; only
                # the theme screen proves the bundled app-server answered.
                if next_enter is not None and time.monotonic() >= next_enter:
                    os.write(master_fd, b"\r")
                    next_enter = time.monotonic() + 2
                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if not readable:
                    continue
                try:
                    chunk = os.read(master_fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                output.extend(chunk)
                if b"Traceback" in output:
                    _fail(output.decode("utf-8", errors="replace"))
                screen = _screen_text(output)
                if next_enter is None and "Welcometo" in screen:
                    next_enter = time.monotonic()
                if "Selectyourpreferredtheme" in screen:
                    print("PASS: --setup rendered the onboarding wizard")
                    return
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            os.close(master_fd)

    _fail(
        "--setup did not render the onboarding wizard before timeout\n"
        + output.decode("utf-8", errors="replace")[-2000:]
    )


async def test_acp_session(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # The session must see the bundled skills only, not the user's.
        home = Path(tmp) / "home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["VIBE_HOME"] = str(home / ".vibe")
        env["MISTRAL_API_KEY"] = "smoke-test-mock-key"
        env["VIBE_TEST_DISABLE_KEYRING"] = "1"

        proc = await asyncio.create_subprocess_exec(
            str(binary),
            stdin=aio_subprocess.PIPE,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
        )
        try:
            assert proc.stdin is not None
            assert proc.stdout is not None

            client = _SmokeClient()
            conn = connect_to_agent(client, proc.stdin, proc.stdout)
            resp = await asyncio.wait_for(
                conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(
                        name="smoke-test", title="Smoke Test", version="0.0.0"
                    ),
                ),
                timeout=15,
            )

            if resp.protocol_version != PROTOCOL_VERSION:
                _fail(
                    f"protocol version mismatch: {resp.protocol_version} != {PROTOCOL_VERSION}"
                )
            if resp.agent_info is None:
                _fail("agent_info is None")
            if resp.agent_info.name != "@mistralai/mistral-vibe":
                _fail(f"unexpected agent name: {resp.agent_info.name}")

            print(
                f"PASS: ACP initialize -> {resp.agent_info.name} v{resp.agent_info.version}"
            )

            await asyncio.wait_for(conn.new_session(cwd=tmp, mcp_servers=[]), 60)
            commands = await asyncio.wait_for(client.commands, 15)
            if "vibe:skill-creator" not in commands:
                _fail(
                    f"the built-in vibe plugin is missing from the bundle: {commands}"
                )
            print("PASS: ACP session offers the built-in plugin skills")
        finally:
            await _terminate(proc)


# ---------------------------------------------------------------------------
# Executable-stack detection (Linux only)
# ---------------------------------------------------------------------------

_PT_GNU_STACK = 0x6474E551
_PF_X = 0x1


def _has_executable_stack(filepath: Path) -> bool | None:
    """Check if an ELF binary has executable stack (GNU_STACK with PF_X).

    Returns True if execstack is set, False if clear, None if not an ELF file.
    """
    try:
        with open(filepath, "rb") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return None

            ei_class = f.read(1)[0]  # 1 = 32-bit, 2 = 64-bit
            ei_data = f.read(1)[0]  # 1 = LE, 2 = BE

            if ei_data == 1:
                endian = "<"
            elif ei_data == 2:
                endian = ">"
            else:
                return None

            if ei_class == 2:  # 64-bit
                f.seek(32)
                (e_phoff,) = struct.unpack(f"{endian}Q", f.read(8))
                f.seek(54)
                (e_phentsize,) = struct.unpack(f"{endian}H", f.read(2))
                (e_phnum,) = struct.unpack(f"{endian}H", f.read(2))

                for i in range(e_phnum):
                    f.seek(e_phoff + i * e_phentsize)
                    (p_type,) = struct.unpack(f"{endian}I", f.read(4))
                    (p_flags,) = struct.unpack(f"{endian}I", f.read(4))
                    if p_type == _PT_GNU_STACK:
                        return bool(p_flags & _PF_X)

            elif ei_class == 1:  # 32-bit
                f.seek(28)
                (e_phoff,) = struct.unpack(f"{endian}I", f.read(4))
                f.seek(42)
                (e_phentsize,) = struct.unpack(f"{endian}H", f.read(2))
                (e_phnum,) = struct.unpack(f"{endian}H", f.read(2))

                for i in range(e_phnum):
                    off = e_phoff + i * e_phentsize
                    f.seek(off)
                    (p_type,) = struct.unpack(f"{endian}I", f.read(4))
                    # 32-bit phdr: p_flags is at offset 24 within the entry
                    f.seek(off + 24)
                    (p_flags,) = struct.unpack(f"{endian}I", f.read(4))
                    if p_type == _PT_GNU_STACK:
                        return bool(p_flags & _PF_X)

            # No GNU_STACK header → no executable stack requirement
            return False
    # Keep the parenthesized form: the almalinux:8 smoke leg in
    # .github/workflows/build-and-upload.yml runs this script with python3.11,
    # which rejects the PEP 758 `except A, B:` syntax.
    except (OSError, struct.error):  # fmt: skip
        return None


def test_no_executable_stack(binary_dir: Path) -> None:
    """Verify no ELF binaries in the bundle require executable stack.

    Executable stack (GNU_STACK RWE) is rejected by hardened Linux kernels
    (SELinux enforcing on Fedora, RHEL, etc.). Symptom:

      [PYI-9483:ERROR] Failed to load Python shared library
        '.../libpython3.12.so.1.0': cannot enable executable stack
        as shared object requires: Invalid argument
    """
    if platform.system() != "Linux":
        print("SKIP: executable stack check (not Linux)")
        return

    internal_dir = binary_dir / "_internal"
    if not internal_dir.exists():
        _fail(f"_internal directory not found at {internal_dir}")

    violations: list[Path] = []
    checked = 0

    # Check main binary + everything under _internal/
    candidates = [binary_dir / "vibe-acp"]
    candidates.extend(internal_dir.rglob("*"))

    for filepath in candidates:
        if not filepath.is_file():
            continue
        result = _has_executable_stack(filepath)
        if result is None:
            continue  # not ELF
        checked += 1
        if result:
            violations.append(filepath)

    if violations:
        lines = [
            f"Found {len(violations)} ELF file(s) with executable stack "
            f"(GNU_STACK RWE) out of {checked} checked.",
            "",
            "These will FAIL on SELinux-enforcing systems (Fedora, RHEL, hardened kernels):",
        ]
        for v in violations:
            lines.append(f"  - {v.relative_to(binary_dir)}")
        lines.append("")
        lines.append("Fix: run 'patchelf --clear-execstack' on these files.")
        _fail("\n".join(lines))

    print(f"PASS: no executable stack in {checked} ELF files")


def main() -> None:
    args = sys.argv[1:]
    quick = "--quick" in args
    paths = [arg for arg in args if arg != "--quick"]
    if len(paths) != 1:
        print(f"Usage: {sys.argv[0]} [--quick] <binary-dir>")
        sys.exit(1)

    # Absolute: some checks run the binary from a temporary working directory.
    binary_dir = Path(paths[0]).resolve()
    binary_name = "vibe-acp.exe" if platform.system() == "Windows" else "vibe-acp"
    binary = binary_dir / binary_name

    if not binary.exists():
        _fail(f"binary not found at {binary}")

    if platform.system() != "Windows":
        binary.chmod(0o755)

    print(f"Testing binary: {binary}\n")

    test_version(binary)
    test_bundled_rust_tui(binary_dir)
    asyncio.run(test_setup_app_server_mode(binary))
    if quick:
        print("\nQuick smoke tests passed!")
        return
    test_setup_renders_onboarding(binary)
    test_no_executable_stack(binary_dir)
    asyncio.run(test_acp_session(binary))

    print("\nAll smoke tests passed!")


if __name__ == "__main__":
    main()
