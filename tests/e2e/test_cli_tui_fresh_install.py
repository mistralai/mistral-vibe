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


def _build_wheel(dist_dir: Path) -> Path:
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=TESTS_ROOT.parent,
        check=True,
    )
    wheels = sorted(dist_dir.glob("mistral_vibe-*.whl"))
    assert len(wheels) == 1
    assert "-cp312-abi3-" in wheels[0].name
    # The global test fixture mocks sys.platform to Linux; platform.system()
    # reflects the host that actually produced the wheel.
    if platform.system() == "Linux":
        assert wheels[0].name.endswith(
            f"-cp312-abi3-manylinux_2_28_{platform.machine()}.whl"
        )
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = wheel.namelist()
        assert any(name.startswith("vibe/_bin/vibe-rs") for name in names)
        assert any(
            name.startswith("mistralai_vibe_local_harness/_native.") for name in names
        )
    return wheels[0]


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
    wheel_path = _build_wheel(tmp_path / "dist")
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
