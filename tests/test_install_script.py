from __future__ import annotations

from pathlib import Path
import re
import shlex
import stat
import subprocess
from textwrap import dedent

INSTALL_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _pinned_uv_installer_sha256() -> str:
    match = re.search(
        r'^UV_INSTALLER_SHA256="([0-9a-f]+)"', INSTALL_SCRIPT.read_text(), re.MULTILINE
    )
    assert match is not None, "could not find UV_INSTALLER_SHA256 in install.sh"
    return match.group(1)


_FAKE_VIBE_SCRIPT = """#!/usr/bin/env bash
exit 0
"""

_FAKE_UV_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail

tool_bin_dir="${UV_TOOL_BIN_DIR:?UV_TOOL_BIN_DIR must be set}"

# Like real uv, color even captured output under FORCE_COLOR unless given --color never.
color="${FORCE_COLOR:-}"
if [[ "${1:-}" == "--color" ]]; then
  [[ "$2" == "never" ]] && color=""
  shift 2
fi
paint() {
  if [[ -n "$color" ]]; then printf '\\033[1m%s\\033[0m\\n' "$1"; else printf '%s\\n' "$1"; fi
}

# install.sh pins the GIL build of Python 3.14 with `--python cpython-3.14+gil`.
case "$*" in
  "--version")
    echo "uv 0.test"
    ;;
  "tool dir --bin")
    paint "$tool_bin_dir"
    ;;
  "tool list")
    paint "${UV_FAKE_TOOL_LIST:-No tools installed}"
    ;;
  "tool install --python cpython-3.14+gil --force mistral-vibe"|"tool upgrade --python cpython-3.14+gil mistral-vibe")
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
        output=""
        while [[ $# -gt 0 ]]; do
          case "$1" in
            -o) output="$2"; shift 2 ;;
            *) shift ;;
          esac
        done
        if [[ -n "$output" ]]; then
          cat {shlex.quote(str(payload_path))} >"$output"
        else
          cat {shlex.quote(str(payload_path))}
        fi
        """,
    )


def _write_fake_checksum_tools(bin_dir: Path, sha256: str) -> None:
    for name in ("sha256sum", "shasum"):
        _write_executable(
            bin_dir / name,
            f"""\
            #!/usr/bin/env bash
            file="${{@: -1}}"
            echo "{sha256}  $file"
            """,
        )


def _write_fake_mktemp(path: Path, installer_tmp: Path) -> None:
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        touch {shlex.quote(str(installer_tmp))}
        printf '%s\\n' {shlex.quote(str(installer_tmp))}
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
    installer_tmp = tmp_path / "downloaded-uv-installer.sh"
    _write_fake_uv_installer(installer_payload)
    _write_fake_curl(fake_bin / "curl", installer_payload)
    _write_fake_checksum_tools(fake_bin, _pinned_uv_installer_sha256())
    _write_fake_mktemp(fake_bin / "mktemp", installer_tmp)

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
    assert not installer_tmp.exists()


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
          "--color never tool dir --bin") echo "$tool_bin_dir" ;;
          "--color never tool list") echo "No tools installed" ;;
          "tool install --python cpython-3.14+gil --force mistral-vibe") mkdir -p "$tool_bin_dir" ;;
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

    result = _run_install_script(
        home,
        [fake_bin],
        {
            "UV_TOOL_BIN_DIR": str(fake_bin),
            "UV_FAKE_TOOL_LIST": "mistral-vibe v0.test\n- vibe\n- vibe-acp",
        },
    )

    assert result.returncode == 0
    assert "Updating mistral-vibe from GitHub repository using uv..." in result.stdout
    assert (
        "Installing mistral-vibe from GitHub repository using uv..."
        not in result.stdout
    )


def test_update_detects_the_uv_tool_when_color_is_forced(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    uv_tool_bin = tmp_path / "uv-tools"
    for directory in (home, fake_bin, uv_tool_bin):
        directory.mkdir()
    _write_fake_uv(fake_bin / "uv")

    result = _run_install_script(
        home,
        [fake_bin],
        {
            "UV_TOOL_BIN_DIR": str(uv_tool_bin),
            "UV_FAKE_TOOL_LIST": "mistral-vibe v0.test",
            "FORCE_COLOR": "1",
        },
    )

    assert "Updating mistral-vibe from GitHub repository using uv..." in result.stdout
    assert f'export PATH="{uv_tool_bin}:$PATH"' in result.stderr


def test_install_with_uv_when_vibe_on_path_is_not_a_uv_tool(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    pip_bin = tmp_path / "pip-bin"
    uv_tool_bin = tmp_path / "uv-tools"
    for directory in (home, fake_bin, pip_bin, uv_tool_bin):
        directory.mkdir()
    log = tmp_path / "uv-invocations.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=False)
    _write_fake_vibe(pip_bin / "vibe")

    result = _run_install_script(
        home,
        [pip_bin, fake_bin, uv_tool_bin],
        {"UV_TOOL_BIN_DIR": str(uv_tool_bin), "UV_FAKE_INVOCATIONS_LOG": str(log)},
    )

    assert result.returncode == 0, result.stderr
    invocations = log.read_text().splitlines()
    assert "tool install --python cpython-3.14+gil --force mistral-vibe" in invocations
    assert not any(line.startswith("tool upgrade") for line in invocations)
    assert f"Another 'vibe' at {pip_bin / 'vibe'} runs instead" in result.stdout
    assert "You can now run vibe" not in result.stdout


def test_update_prefers_the_uv_tool_over_homebrew(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    brew_bin = tmp_path / "brew-bin"
    uv_tool_bin = tmp_path / "uv-tools"
    for directory in (home, fake_bin, brew_bin, uv_tool_bin):
        directory.mkdir()
    brew_log = tmp_path / "brew.log"
    _write_fake_uv(fake_bin / "uv")
    _write_fake_brew(brew_bin / "brew", brew_log)
    _write_fake_vibe(brew_bin / "vibe")

    result = _run_install_script(
        home,
        [brew_bin, fake_bin, uv_tool_bin],
        {
            "UV_TOOL_BIN_DIR": str(uv_tool_bin),
            "UV_FAKE_TOOL_LIST": "mistral-vibe v0.test",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "Updating mistral-vibe from GitHub repository using uv..." in result.stdout
    assert not brew_log.exists()
    assert f"Another 'vibe' at {brew_bin / 'vibe'} runs instead" in result.stdout


def test_homebrew_install_upgrades_with_brew(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    log = tmp_path / "uv-invocations.log"
    brew_log = tmp_path / "brew.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=False)
    _write_fake_brew(fake_bin / "brew", brew_log)
    _write_fake_vibe(fake_bin / "vibe")

    result = _run_install_script(
        home,
        [fake_bin],
        {"UV_TOOL_BIN_DIR": str(fake_bin), "UV_FAKE_INVOCATIONS_LOG": str(log)},
    )

    assert result.returncode == 0, result.stderr
    assert brew_log.read_text().splitlines() == [
        "list --formula mistral-vibe",
        "upgrade mistral-vibe",
        "--prefix",
    ]
    assert log.read_text().splitlines() == ["--version", "--color never tool list"]


def test_homebrew_install_without_uv_does_not_install_uv(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    brew_log = tmp_path / "brew.log"
    curl_log = tmp_path / "curl.log"
    _write_fake_brew(fake_bin / "brew", brew_log)
    _write_fake_vibe(fake_bin / "vibe")
    _write_executable(
        fake_bin / "curl",
        f"""\
        #!/usr/bin/env bash
        echo "$*" >>{shlex.quote(str(curl_log))}
        exit 1
        """,
    )

    result = _run_install_script(home, [fake_bin], {})

    assert result.returncode == 0, result.stderr
    assert "upgrade mistral-vibe" in brew_log.read_text().splitlines()
    assert not curl_log.exists()


def test_homebrew_upgrade_warns_when_another_vibe_comes_first(tmp_path: Path) -> None:
    home = tmp_path / "home"
    pip_bin = tmp_path / "pip-bin"
    brew_bin = tmp_path / "brew" / "bin"
    for directory in (home, pip_bin, brew_bin):
        directory.mkdir(parents=True)
    _write_fake_vibe(pip_bin / "vibe")
    _write_fake_brew(brew_bin / "brew", tmp_path / "brew.log")
    _write_fake_vibe(brew_bin / "vibe")

    result = _run_install_script(home, [pip_bin, brew_bin], {})

    assert result.returncode == 0, result.stderr
    assert (
        f"Another 'vibe' at {pip_bin / 'vibe'} runs instead of the new one at "
        f"{brew_bin / 'vibe'}" in result.stdout
    )


def test_install_warns_when_replacing_a_vibe_from_another_installer(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    log = tmp_path / "uv-invocations.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=False)
    _write_fake_vibe(fake_bin / "vibe")

    result = _run_install_script(
        home,
        [fake_bin],
        {"UV_TOOL_BIN_DIR": str(fake_bin), "UV_FAKE_INVOCATIONS_LOG": str(log)},
    )

    assert result.returncode == 0, result.stderr
    assert f"Replaced an existing 'vibe' in {fake_bin}" in result.stdout


def test_homebrew_upgrade_failure_is_reported(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    _write_fake_vibe(fake_bin / "vibe")
    _write_executable(
        fake_bin / "brew",
        """\
        #!/usr/bin/env bash
        [[ "$*" == "list --formula mistral-vibe" ]]
        """,
    )

    result = _run_install_script(home, [fake_bin], {})

    assert result.returncode == 1
    assert "Homebrew could not upgrade mistral-vibe" in result.stderr


def _write_fake_brew(path: Path, log: Path) -> None:
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        echo "$*" >>{shlex.quote(str(log))}
        case "$*" in
          "list --formula mistral-vibe"|"upgrade mistral-vibe") ;;
          "--prefix") dirname {shlex.quote(str(path.parent))} ;;
          *) exit 1 ;;
        esac
        """,
    )


def _write_recording_uv(path: Path, *, fail_install_until_seeded: bool) -> None:
    """A fake uv that records every invocation to UV_FAKE_INVOCATIONS_LOG.

    With `fail_install_until_seeded`, the pinned `tool install` fails until
    `python install cpython-3.14` has run — uv cannot download a `+gil`
    request, so install.sh must seed a managed GIL build and retry.
    """
    fail_when = (
        '[[ -n "$state" && ! -f "$state" ]]' if fail_install_until_seeded else "false"
    )
    _write_executable(
        path,
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        tool_bin_dir="${{UV_TOOL_BIN_DIR:?UV_TOOL_BIN_DIR must be set}}"
        log="${{UV_FAKE_INVOCATIONS_LOG:?UV_FAKE_INVOCATIONS_LOG must be set}}"
        state="${{UV_FAKE_SEED_STATE:-}}"

        echo "$*" >>"$log"
        echo "${{UV_SYSTEM_CERTS-unset}}" >>"${{UV_FAKE_SYSTEM_CERTS_LOG:-/dev/null}}"

        case "$*" in
          "--version") echo "uv 0.test" ;;
          "--color never tool dir --bin") echo "$tool_bin_dir" ;;
          "--color never tool list") echo "No tools installed" ;;
          "python install cpython-3.14") touch "$state" ;;
          "tool install --python cpython-3.14+gil --force mistral-vibe")
            if {fail_when}; then
              echo "error: No interpreter found for cpython-3.14+gil-any-any-any" >&2
              exit 1
            fi
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
          *) echo "unexpected uv invocation: $*" >&2; exit 1 ;;
        esac
        """,
    )


def test_install_pins_the_gil_build_of_python_314(tmp_path: Path) -> None:
    """install.sh forces the GIL-enabled build of Python 3.14 through uv."""
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    log = tmp_path / "uv-invocations.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=False)

    result = _run_install_script(
        home,
        [fake_bin],
        {"UV_TOOL_BIN_DIR": str(fake_bin), "UV_FAKE_INVOCATIONS_LOG": str(log)},
    )

    assert result.returncode == 0
    invocations = log.read_text().splitlines()
    assert "tool install --python cpython-3.14+gil --force mistral-vibe" in invocations


def test_install_seeds_a_managed_gil_python_when_the_pinned_request_fails(
    tmp_path: Path,
) -> None:
    """uv cannot download a `+gil` request: install.sh seeds a managed GIL
    3.14 with `uv python install cpython-3.14` and retries.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    state = tmp_path / "seeded"
    log = tmp_path / "uv-invocations.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=True)

    result = _run_install_script(
        home,
        [fake_bin],
        {
            "UV_TOOL_BIN_DIR": str(fake_bin),
            "UV_FAKE_SEED_STATE": str(state),
            "UV_FAKE_INVOCATIONS_LOG": str(log),
        },
    )

    assert result.returncode == 0
    assert "Installation completed successfully!" in result.stdout
    invocations = log.read_text().splitlines()
    install = "tool install --python cpython-3.14+gil --force mistral-vibe"
    seed = "python install cpython-3.14"
    first = invocations.index(install)
    assert invocations[first + 1] == seed
    assert invocations[first + 2] == install
    assert state.exists()


def _uv_system_certs_seen(tmp_path: Path, extra_env: dict[str, str]) -> set[str]:
    home = tmp_path / "home"
    fake_bin = tmp_path / "fake-bin"
    home.mkdir()
    fake_bin.mkdir()
    system_certs_log = tmp_path / "uv-system-certs.log"
    _write_recording_uv(fake_bin / "uv", fail_install_until_seeded=False)

    result = _run_install_script(
        home,
        [fake_bin],
        {
            "UV_TOOL_BIN_DIR": str(fake_bin),
            "UV_FAKE_INVOCATIONS_LOG": str(tmp_path / "uv-invocations.log"),
            "UV_FAKE_SYSTEM_CERTS_LOG": str(system_certs_log),
            **extra_env,
        },
    )

    assert result.returncode == 0
    return set(system_certs_log.read_text().splitlines())


def test_install_makes_uv_trust_the_system_certificate_store(tmp_path: Path) -> None:
    """A TLS-inspecting proxy's root CA lives in the OS trust store, which uv
    ignores unless asked.
    """
    assert _uv_system_certs_seen(tmp_path, {}) == {"true"}


def test_install_keeps_an_explicit_uv_system_certs_choice(tmp_path: Path) -> None:
    assert _uv_system_certs_seen(tmp_path, {"UV_SYSTEM_CERTS": "false"}) == {"false"}


def test_install_keeps_a_legacy_uv_native_tls_choice(tmp_path: Path) -> None:
    assert _uv_system_certs_seen(tmp_path, {"UV_NATIVE_TLS": "false"}) == {"unset"}
