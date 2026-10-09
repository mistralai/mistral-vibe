from __future__ import annotations

import io
import os
from pathlib import Path
import platform
import subprocess
import sys
import zipfile

import pexpect
import pytest

pytest.importorskip("pty")

from tests import TESTS_ROOT
from tests.e2e.common import (
    ansi_tolerant_pattern,
    send_ctrl_c_until_quit_confirmation,
    wait_for_main_screen,
    wait_for_request_count_while_draining_child_output,
)
from tests.e2e.mock_server import StreamingMockServer


def _venv_executable(venv_path: Path, name: str) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / f"{name}.exe"
    return venv_path / "bin" / name


# CI builds the combined wheel once and passes it here, so this test does not
# compile both Rust artifacts again. Unset, the test builds its own.
_PREBUILT_WHEEL_ENV = "VIBE_PREBUILT_WHEEL"
_HARNESS_WHEEL_ENV = "VIBE_PREBUILT_HARNESS_WHEEL"
_RUST_TUI_ENV = "VIBE_PREBUILT_RUST_TUI"

# The two native artifacts come from their own projects, and the combined wheel
# is assembled from them, exactly like CI's combined-wheel step. Building it in
# this checkout instead swaps the bundled Harness runtime for a pure-Python
# copy for the whole build, and any parallel test importing the package during
# that swap fails with ModuleNotFoundError.
_HARNESS_PYTHON_PROJECT = (
    TESTS_ROOT.parent.parent / "agents" / "harness" / "harness" / "runtimes" / "python"
)
_CLI_RUST_DIR = TESTS_ROOT.parent / "vibe" / "cli-rust"
_TUI_NAME = "vibe-rs.exe" if platform.system() == "Windows" else "vibe-rs"


def _build_harness_wheel(harness_dist: Path) -> Path:
    # Build the Harness wheel the way CI's local-harness-wheel step does: with
    # maturin directly and, on Linux, zig + manylinux_2_28. A plain
    # `uv build --wheel` there tags the wheel for the host glibc instead, and
    # the assembled combined wheel inherits that tag, failing the manylinux
    # assertion in ``_combined_wheel``.
    # The sync exact-matches the lockfile without installing the project
    # itself, so an editable Harness install in that venv is removed; the
    # next `uv sync` in the project restores it.
    subprocess.run(
        [
            "uv",
            "sync",
            "--frozen",
            "--no-install-project",
            "--directory",
            str(_HARNESS_PYTHON_PROJECT),
        ],
        check=True,
    )
    maturin_command = [
        "uv",
        "run",
        "--no-sync",
        "--directory",
        str(_HARNESS_PYTHON_PROJECT),
        "maturin",
        "build",
        "--release",
        "--locked",
        "--out",
        str(harness_dist),
    ]
    build_environment = {
        **os.environ,
        # CI's own wheel directory, separate from the one the Harness build
        # backend manages: it wipes its directory on fingerprint changes,
        # and this build does not hold the backend's lock.
        "CARGO_TARGET_DIR": str(
            _HARNESS_PYTHON_PROJECT / ".cache" / "cargo-target-ci-wheel"
        ),
    }
    # The global test fixture mocks sys.platform to Linux; platform.system()
    # reflects the host that actually runs the build.
    if platform.system() == "Darwin":
        # Stripped Mach-O images are refused by dyld on macOS 26+
        # (rust-lang/rust#157750); keep the built extension importable, like
        # the vibe build backend does.
        build_environment["CARGO_PROFILE_RELEASE_STRIP"] = "none"
    if platform.system() == "Linux":
        maturin_command += ["--zig", "--compatibility", "manylinux_2_28"]
    subprocess.run(maturin_command, check=True, env=build_environment)
    harness_wheels = sorted(harness_dist.glob("mistralai_vibe_local_harness-*.whl"))
    assert len(harness_wheels) == 1
    return harness_wheels[0]


def _build_wheel(dist_dir: Path) -> Path:
    harness_wheel = _build_harness_wheel(dist_dir / "harness-wheel")
    subprocess.run(
        [
            "cargo",
            "build",
            "--release",
            "--locked",
            "--manifest-path",
            str(_CLI_RUST_DIR / "Cargo.toml"),
            "--bin",
            "vibe-rs",
        ],
        check=True,
    )
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=TESTS_ROOT.parent,
        check=True,
        env={
            **os.environ,
            _HARNESS_WHEEL_ENV: str(harness_wheel),
            _RUST_TUI_ENV: str(_CLI_RUST_DIR / "target" / "release" / _TUI_NAME),
        },
    )
    wheels = sorted(dist_dir.glob("mistral_vibe-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _combined_wheel(dist_dir: Path) -> Path:
    prebuilt = os.environ.get(_PREBUILT_WHEEL_ENV)
    wheel_path = Path(prebuilt) if prebuilt else _build_wheel(dist_dir)
    assert wheel_path.name.startswith("mistral_vibe-")
    assert "-cp312-abi3-" in wheel_path.name
    # The global test fixture mocks sys.platform to Linux; platform.system()
    # reflects the host that actually produced the wheel.
    if platform.system() == "Linux":
        assert wheel_path.name.endswith(
            f"-cp312-abi3-manylinux_2_28_{platform.machine()}.whl"
        )
    with zipfile.ZipFile(wheel_path) as wheel:
        names = wheel.namelist()
        assert any(name.startswith("vibe/_bin/vibe-rs") for name in names)
        assert any(
            name.startswith("mistralai_vibe_local_harness/_native.") for name in names
        )
    return wheel_path


def _install_fresh_wheel(tmp_path: Path, wheel_path: Path) -> Path:
    venv_path = tmp_path / "fresh-install-venv"
    subprocess.run(
        ["uv", "venv", "--no-config", "--python", sys.executable, str(venv_path)],
        cwd=tmp_path,
        check=True,
    )

    python_path = _venv_executable(venv_path, "python")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--no-config",
            "--refresh",
            "--exclude-newer-package",
            "mistralai-vibe-local-harness=false",
            "--python",
            str(python_path),
            str(wheel_path),
        ],
        cwd=tmp_path,
        check=True,
    )
    return _venv_executable(venv_path, "vibe")


@pytest.mark.timeout(900)
def test_fresh_wheel_install_can_spawn_cli_and_complete_happy_path(
    streaming_mock_server: StreamingMockServer,
    setup_e2e_env: None,
    e2e_workdir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_SKIP_RUST_TUI", raising=False)
    monkeypatch.delenv("VIBE_CLI", raising=False)
    wheel_path = _combined_wheel(tmp_path / "dist")
    vibe_executable = _install_fresh_wheel(tmp_path, wheel_path)

    monkeypatch.delenv("PYTHONPATH", raising=False)

    captured = io.StringIO()
    child = pexpect.spawn(
        str(vibe_executable),
        ["--workdir", str(e2e_workdir)],
        cwd=str(tmp_path),
        env=os.environ,
        encoding="utf-8",
        timeout=30,
        dimensions=(36, 120),
    )
    child.logfile_read = captured

    try:
        wait_for_main_screen(child, timeout=20)
        child.send("Greet")
        child.send("\r")

        wait_for_request_count_while_draining_child_output(
            child,
            captured,
            lambda: len(streaming_mock_server.requests),
            expected_count=1,
            timeout=10,
        )
        child.expect(ansi_tolerant_pattern("Hello from mock server"), timeout=10)

        send_ctrl_c_until_quit_confirmation(child, captured, timeout=5)
        child.expect(pexpect.EOF, timeout=10)
    finally:
        if child.isalive():
            child.terminate(force=True)
        if not child.closed:
            child.close()

    output = captured.getvalue()
    assert "Welcome to Mistral Vibe" not in output
    assert streaming_mock_server.requests[-1].get("model") == "mock-model"
