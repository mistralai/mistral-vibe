from __future__ import annotations

from pathlib import Path
import shlex
import stat
import subprocess
from textwrap import dedent

INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"

_FAKE_VIBE_SCRIPT = """#!/usr/bin/env bash
exit 0
"""

_FAKE_UV_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

tool_bin_dir="${UV_TOOL_BIN_DIR:?UV_TOOL_BIN_DIR must be set}"

case "$*" in
  "--version")
    echo "uv 0.test"
    ;;
  "tool dir --bin")
    echo "$tool_bin_dir"
    ;;
  "tool install mistral-vibe"|"tool upgrade mistral-vibe")
    mkdir -p "$tool_bin_dir"
    cat >"$tool_bin_dir/vibe" <<'VIBE'
#!/usr/bin/env bash
exit 0
VIBE
    chmod +x "$tool_bin_dir/vibe"
    cat >"$tool_bin_dir/vibe-acp" <<'VIBE_ACP'
#!/usr/bin/env bash
exit 0
VIBE_ACP
    chmod +x "$tool_bin_dir/vibe-acp"
    ;;
  *)
    echo "unexpected uv invocation: $*" >&2
    exit 1
    ;;
esac
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(dedent(content))
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_fake_uv(path: Path) -> None:
    _write_executable(path, _FAKE_UV_SCRIPT)


def _write_fake_vibe(path: Path) -> None:
    _write_executable(path, _FAKE_VIBE_SCRIPT)


def _write_fake_uv_installer(payload_path: Path) -> None:
    payload_path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "\n"
        'mkdir -p "$HOME/.local/bin"\n'
        "cat >\"$HOME/.local/bin/uv\" <<'UV'\n"
        f"{_FAKE_UV_SCRIPT}"
        "UV\n"
        'chmod +x "$HOME/.local/bin/uv"\n'
    )


def _write_fake_curl(path: Path, payload_path: Path) -> None:
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        cat {shlex.quote(str(payload_path))}
        """,
    )


def _run_install_script(
    home: Path, path_entries: list[Path], extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(home),
        "PATH": ":".join([*(str(entry) for entry in path_entries), "/usr/bin", "/bin"]),
        "TERM": "dumb",
        **extra_env,
    }

    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT)],
        capture_output=True,
        check=False,
        cwd=INSTALL_SCRIPT.parent.parent,
        env=env,
        text=True,
        timeout=30,
    )


def test_install_reports_missing_path_for_uv_tool_bin(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()

    installer_payload = tmp_path / "fake-uv-installer.sh"
    _write_fake_uv_installer(installer_payload)
    _write_fake_curl(fake_bin / "curl", installer_payload)

    uv_bin_dir = home / ".local" / "bin"
    result = _run_install_script(home, [fake_bin], {"UV_TOOL_BIN_DIR": str(uv_bin_dir)})

    assert result.returncode == 1
    assert (
        "Your PATH does not include the folder that contains 'vibe'." in result.stderr
    )
    assert f'export PATH="{uv_bin_dir}:$PATH"' in result.stderr
    assert (
        result.stderr.count(
            "Add this directory to your shell profile, then restart your terminal:"
        )
        == 1
    )
    assert (
        "uv was installed but not found in PATH for this session" not in result.stdout
    )


def test_install_succeeds_when_uv_bin_dir_is_already_on_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    _write_fake_uv(fake_bin / "uv")

    result = _run_install_script(home, [fake_bin], {"UV_TOOL_BIN_DIR": str(fake_bin)})

    assert result.returncode == 0
    assert "Installation completed successfully!" in result.stdout
    assert (fake_bin / "vibe").exists()
    assert (fake_bin / "vibe-acp").exists()


def test_install_fails_when_vibe_not_in_uv_tool_dir(tmp_path: Path) -> None:
    """Covers the fallback error when uv tool dir doesn't contain a vibe binary."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()

    # Create a fake uv that does NOT produce a vibe binary on install
    _write_executable(
        fake_bin / "uv",
        """\
        #!/usr/bin/env bash
        set -euo pipefail
        tool_bin_dir="${UV_TOOL_BIN_DIR:?UV_TOOL_BIN_DIR must be set}"
        case "$*" in
          "--version") echo "uv 0.test" ;;
          "tool dir --bin") echo "$tool_bin_dir" ;;
          "tool install mistral-vibe") mkdir -p "$tool_bin_dir" ;;
          *) echo "unexpected: $*" >&2; exit 1 ;;
        esac
        """,
    )

    uv_tool_bin = tmp_path / "uv-tools"
    uv_tool_bin.mkdir()
    result = _run_install_script(
        home, [fake_bin], {"UV_TOOL_BIN_DIR": str(uv_tool_bin)}
    )

    assert result.returncode == 1
    assert "uv did not expose a 'vibe' executable" in result.stderr
    assert "Your PATH does not include" not in result.stderr


def test_update_succeeds_when_vibe_is_already_on_path(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    _write_fake_uv(fake_bin / "uv")
    _write_fake_vibe(fake_bin / "vibe")

    result = _run_install_script(home, [fake_bin], {"UV_TOOL_BIN_DIR": str(fake_bin)})

    assert result.returncode == 0
    assert "Updating mistral-vibe from GitHub repository using uv..." in result.stdout
    assert (
        "Installing mistral-vibe from GitHub repository using uv..."
        not in result.stdout
    )
