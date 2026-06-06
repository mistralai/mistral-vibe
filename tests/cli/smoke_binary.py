#!/usr/bin/env python3
"""Smoke tests for the built vibe binary.

Usage: python tests/cli/smoke_binary.py <binary-dir>

Tests:
  1. --version exits successfully
  2. Normal interactive launch starts far enough to load the main Textual app
  3. --setup starts far enough to load bundled setup/Textual assets
  4. Programmatic mode without an API key fails with the expected config error
  5. Runtime data files are present in the bundle
  6. The relocated bundle can be launched from PATH
  7. (Linux) No ELF binaries require executable stack (GNU_STACK RWE)
"""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import time
from typing import NoReturn


def _fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _isolated_env(vibe_home: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["VIBE_HOME"] = str(vibe_home)
    env["TERM"] = env.get("TERM") or "xterm-256color"
    env.pop("MISTRAL_API_KEY", None)
    env.pop("VIBE_MISTRAL_API_KEY", None)
    return env


def test_version(binary: Path) -> None:
    result = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        _fail(
            f"--version exited with code {result.returncode}\nstderr: {result.stderr}"
        )
    print(f"PASS: --version -> {result.stdout.strip()}")


def test_interactive_launch_loads_bundled_assets(binary: Path) -> None:
    if platform.system() == "Windows":
        # Windows does not provide the Unix pty module used to drive Textual here.
        print("SKIP: interactive pty smoke test (Windows)")
        return

    import pty
    import select

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        master_fd, slave_fd = pty.openpty()
        env = _isolated_env(tmp_path / "home")
        env["MISTRAL_API_KEY"] = "smoke-test-mock-key"
        proc = subprocess.Popen(
            [str(binary), "--trust", "--workdir", str(workdir)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            text=False,
        )
        os.close(slave_fd)

        output = bytearray()
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break

                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if not readable:
                    continue

                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                output.extend(chunk)

                decoded = output.decode("utf-8", errors="replace")
                if "Traceback" in decoded or "StylesheetError" in decoded:
                    _fail(decoded)

                # This pins the Textual app title used as the main-app smoke marker.
                if "\x1b]0;Vibe\x07" in decoded and len(output) > 4096:
                    print("PASS: interactive launch loaded bundled UI assets")
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

    _fail("interactive launch did not render expected UI output before timeout")


def test_setup_loads_bundled_assets(binary: Path) -> None:
    if platform.system() == "Windows":
        # Windows does not provide the Unix pty module used to drive Textual here.
        print("SKIP: --setup pty smoke test (Windows)")
        return

    import pty
    import select

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp) / "workdir"
        workdir.mkdir()
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            [str(binary), "--setup", "--workdir", str(workdir)],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=_isolated_env(Path(tmp) / "home"),
            text=False,
        )
        os.close(slave_fd)

        output = bytearray()
        deadline = time.monotonic() + 30
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break

                readable, _, _ = select.select([master_fd], [], [], 0.2)
                if not readable:
                    continue

                chunk = os.read(master_fd, 4096)
                if not chunk:
                    break
                output.extend(chunk)

                if b"Traceback" in output or b"StylesheetError" in output:
                    _fail(output.decode("utf-8", errors="replace"))

                if b"Mistral" in output or b"API" in output or b"Welcome" in output:
                    print("PASS: --setup loaded bundled UI assets")
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

    _fail("--setup did not render expected setup UI text before timeout")


def test_programmatic_missing_api_key(binary: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [str(binary), "-p", "hello"],
            capture_output=True,
            env=_isolated_env(Path(tmp) / ".vibe"),
            text=True,
            timeout=30,
        )

    if result.returncode == 0:
        _fail("programmatic mode without API key unexpectedly succeeded")

    output = f"{result.stdout}\n{result.stderr}"
    if "Traceback" in output:
        _fail(f"programmatic mode raised a traceback:\n{output}")
    # This pins the user-facing guidance for the missing API key path.
    if "Set the environment variable" not in output:
        _fail(f"missing expected API key guidance:\n{output}")

    print("PASS: programmatic mode reports missing API key")


def test_bundled_runtime_files(binary_dir: Path) -> None:
    bundle_root = binary_dir / "_internal" / "vibe"
    source_root = Path(__file__).resolve().parents[2] / "vibe"

    if not bundle_root.is_dir():
        _fail(f"bundled vibe package not found at {bundle_root}")
    if not source_root.is_dir():
        _fail(f"source vibe package not found at {source_root}")

    required_exact = [
        "whats_new.md",
        "cli/textual_ui/app.tcss",
        "setup/onboarding/onboarding.tcss",
        "setup/trusted_folders/trust_folder_dialog.tcss",
    ]

    missing_exact = [
        relative
        for relative in required_exact
        if not (bundle_root / relative).is_file()
    ]
    if missing_exact:
        lines = ["Missing required bundled runtime file(s):"]
        lines.extend(f"  - vibe/{relative}" for relative in missing_exact)
        _fail("\n".join(lines))

    mirrored_globs = [
        "**/*.tcss",
        "**/*.md",
        "core/tools/builtins/*.py",
        "core/skills/builtins/*.py",
    ]

    for pattern in mirrored_globs:
        source_files = {
            path.relative_to(source_root)
            for path in source_root.glob(pattern)
            if path.is_file()
        }
        bundled_files = {
            path.relative_to(bundle_root)
            for path in bundle_root.glob(pattern)
            if path.is_file()
        }

        missing = sorted(source_files - bundled_files)
        if missing:
            lines = [f"Bundle is missing runtime file(s) for pattern {pattern}:"]
            lines.extend(f"  - vibe/{path}" for path in missing)
            _fail("\n".join(lines))

    print("PASS: bundled runtime files are present")


def test_installed_bundle_launches(binary_dir: Path, binary_name: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        install_dir = tmp_path / "install" / "vibe"
        workdir = tmp_path / "workdir"
        vibe_home = tmp_path / "home"
        workdir.mkdir()

        shutil.copytree(binary_dir, install_dir)
        installed_binary = install_dir / binary_name
        if not installed_binary.exists():
            _fail(f"installed binary not found at {installed_binary}")
        if platform.system() != "Windows":
            installed_binary.chmod(0o755)

        env = _isolated_env(vibe_home)
        env["PATH"] = f"{install_dir}{os.pathsep}{env.get('PATH', '')}"

        result = subprocess.run(
            [binary_name, "--version"],
            capture_output=True,
            cwd=workdir,
            env=env,
            text=True,
            timeout=30,
        )

    if result.returncode != 0:
        _fail(
            "installed bundle --version exited with code "
            f"{result.returncode}\nstderr: {result.stderr}"
        )
    if "Traceback" in f"{result.stdout}\n{result.stderr}":
        _fail(f"installed bundle raised a traceback:\n{result.stdout}\n{result.stderr}")

    print(f"PASS: installed bundle launches from PATH -> {result.stdout.strip()}")


_PT_GNU_STACK = 0x6474E551
_PF_X = 0x1


def _has_executable_stack(filepath: Path) -> bool | None:
    try:
        with filepath.open("rb") as f:
            magic = f.read(4)
            if magic != b"\x7fELF":
                return None

            ei_class = f.read(1)[0]
            ei_data = f.read(1)[0]

            match ei_data:
                case 1:
                    endian = "<"
                case 2:
                    endian = ">"
                case _:
                    return None

            if ei_class == 2:
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

            elif ei_class == 1:
                f.seek(28)
                (e_phoff,) = struct.unpack(f"{endian}I", f.read(4))
                f.seek(42)
                (e_phentsize,) = struct.unpack(f"{endian}H", f.read(2))
                (e_phnum,) = struct.unpack(f"{endian}H", f.read(2))

                for i in range(e_phnum):
                    off = e_phoff + i * e_phentsize
                    f.seek(off)
                    (p_type,) = struct.unpack(f"{endian}I", f.read(4))
                    f.seek(off + 24)
                    (p_flags,) = struct.unpack(f"{endian}I", f.read(4))
                    if p_type == _PT_GNU_STACK:
                        return bool(p_flags & _PF_X)

            return False
    except (OSError, struct.error):
        return None


def test_no_executable_stack(binary_dir: Path, binary_name: str) -> None:
    if platform.system() != "Linux":
        print("SKIP: executable stack check (not Linux)")
        return

    internal_dir = binary_dir / "_internal"
    if not internal_dir.exists():
        _fail(f"_internal directory not found at {internal_dir}")

    violations: list[Path] = []
    checked = 0
    candidates = [binary_dir / binary_name, *internal_dir.rglob("*")]

    for filepath in candidates:
        if not filepath.is_file():
            continue
        result = _has_executable_stack(filepath)
        if result is None:
            continue
        checked += 1
        if result:
            violations.append(filepath)

    if violations:
        lines = [
            f"Found {len(violations)} ELF file(s) with executable stack "
            f"(GNU_STACK RWE) out of {checked} checked.",
            "",
            "These will fail on SELinux-enforcing or hardened kernels:",
        ]
        for violation in violations:
            lines.append(f"  - {violation.relative_to(binary_dir)}")
        lines.append("")
        lines.append("Fix: run 'patchelf --clear-execstack' on these files.")
        _fail("\n".join(lines))

    print(f"PASS: no executable stack in {checked} ELF files")


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <binary-dir>")
        sys.exit(1)

    binary_dir = Path(sys.argv[1])
    binary_name = "vibe.exe" if platform.system() == "Windows" else "vibe"
    binary = binary_dir / binary_name

    if not binary.exists():
        _fail(f"binary not found at {binary}")

    if platform.system() != "Windows":
        binary.chmod(0o755)

    print(f"Testing binary: {binary}\n")

    test_version(binary)
    test_bundled_runtime_files(binary_dir)
    test_installed_bundle_launches(binary_dir, binary_name)
    test_no_executable_stack(binary_dir, binary_name)
    test_interactive_launch_loads_bundled_assets(binary)
    test_setup_loads_bundled_assets(binary)
    test_programmatic_missing_api_key(binary)

    print("\nAll smoke tests passed!")


if __name__ == "__main__":
    main()
