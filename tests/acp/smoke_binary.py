#!/usr/bin/env python3
"""Smoke tests for the built vibe-acp binary.

Usage: python tests/smoke_binary.py <binary-dir>

Tests:
  1. --version exits successfully
  2. ACP initialize handshake returns expected agent info
  3. (Linux) No ELF binaries require executable stack (GNU_STACK RWE)
"""

from __future__ import annotations

import asyncio
import asyncio.subprocess as aio_subprocess
import contextlib
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
from typing import Any, NoReturn

from acp import PROTOCOL_VERSION, Client, RequestError, connect_to_agent
from acp.schema import ClientCapabilities, Implementation


class _SmokeClient(Client):
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

    async def session_update(self, *_args: Any, **_kwargs: Any) -> None:
        pass


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


async def test_acp_initialize(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vibe_home = Path(tmp) / ".vibe"
        env = os.environ.copy()
        env["VIBE_HOME"] = str(vibe_home)
        env["MISTRAL_API_KEY"] = "smoke-test-mock-key"

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

            conn = connect_to_agent(_SmokeClient(), proc.stdin, proc.stdout)
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
    except (OSError, struct.error):
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
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <binary-dir>")
        sys.exit(1)

    binary_dir = Path(sys.argv[1])
    binary_name = "vibe-acp.exe" if platform.system() == "Windows" else "vibe-acp"
    binary = binary_dir / binary_name

    if not binary.exists():
        _fail(f"binary not found at {binary}")

    if platform.system() != "Windows":
        binary.chmod(0o755)

    print(f"Testing binary: {binary}\n")

    test_version(binary)
    test_no_executable_stack(binary_dir)
    asyncio.run(test_acp_initialize(binary))

    print("\nAll smoke tests passed!")


if __name__ == "__main__":
    main()
