from __future__ import annotations

import os
from pathlib import Path
import runpy
import subprocess

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts/ci/inject-sentry-dsn.sh"
_PYTHON_TARGET = Path("vibe/observability/sentry.py")
_RUST_TARGET = Path("vibe/cli-rust/src/observability/sentry.rs")
_CLI_DSN = "http://cli@127.0.0.1:9/42"
_ACP_DSN = "http://acp@127.0.0.1:9/43"
_RUST_PLACEHOLDER = "const CLI_SENTRY_DSN: Option<&str> = None;\n"


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    python_target = tmp_path / _PYTHON_TARGET
    python_target.parent.mkdir(parents=True)
    python_target.write_text("_CLI_SENTRY_DSN = None\n_ACP_SENTRY_DSN = None\n")
    rust_target = tmp_path / _RUST_TARGET
    rust_target.parent.mkdir(parents=True)
    rust_target.write_text(_RUST_PLACEHOLDER)
    return tmp_path


def _inject(
    root: Path,
    cli: str = "",
    acp: str = "",
    *,
    required: bool = False,
    skip_rust_tui: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SCRIPT)],
        cwd=root,
        env={
            **os.environ,
            "CLI_SENTRY_DSN": cli,
            "ACP_SENTRY_DSN": acp,
            "REQUIRE_SENTRY_DSN": str(required).lower(),
            "VIBE_SKIP_RUST_TUI": "1" if skip_rust_tui else "",
        },
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_release_injects_cli_dsn_into_python_and_rust(release_tree: Path) -> None:
    result = _inject(release_tree, _CLI_DSN, _ACP_DSN, required=True)

    assert result.returncode == 0, result.stderr
    python_values = runpy.run_path(str(release_tree / _PYTHON_TARGET))
    assert python_values["_CLI_SENTRY_DSN"] == _CLI_DSN
    assert python_values["_ACP_SENTRY_DSN"] == _ACP_DSN

    assert (release_tree / _RUST_TARGET).read_text() == (
        f'const CLI_SENTRY_DSN: Option<&str> = Some("{_CLI_DSN}");\n'
    )
    assert not list(release_tree.rglob("*.bak"))


def test_optional_missing_dsns_leave_sources_unchanged(release_tree: Path) -> None:
    result = _inject(release_tree)

    assert result.returncode == 0, result.stderr
    assert (release_tree / _RUST_TARGET).read_text() == _RUST_PLACEHOLDER
    python_values = runpy.run_path(str(release_tree / _PYTHON_TARGET))
    assert python_values["_CLI_SENTRY_DSN"] is None
    assert python_values["_ACP_SENTRY_DSN"] is None


@pytest.mark.parametrize("cli,acp", [("", _ACP_DSN), (_CLI_DSN, "")])
def test_release_requires_both_dsns(release_tree: Path, cli: str, acp: str) -> None:
    result = _inject(release_tree, cli, acp, required=True)

    assert result.returncode != 0
    assert "is required but its value is not set" in result.stderr


def test_release_fails_if_rust_placeholder_is_missing(release_tree: Path) -> None:
    (release_tree / _RUST_TARGET).write_text("// No release placeholder.\n")

    result = _inject(release_tree, _CLI_DSN, _ACP_DSN, required=True)

    assert result.returncode != 0
    assert "placeholder not found" in result.stderr


def test_release_fails_if_rust_source_is_missing(release_tree: Path) -> None:
    (release_tree / _RUST_TARGET).unlink()

    result = _inject(release_tree, _CLI_DSN, _ACP_DSN, required=True)

    assert result.returncode != 0


def test_rust_dsn_preserves_sed_metacharacters(release_tree: Path) -> None:
    dsn = f"{_CLI_DSN}?first=1&second=a|b"

    result = _inject(release_tree, dsn, _ACP_DSN, required=True)

    assert result.returncode == 0, result.stderr
    assert (release_tree / _RUST_TARGET).read_text() == (
        f'const CLI_SENTRY_DSN: Option<&str> = Some("{dsn}");\n'
    )


@pytest.mark.parametrize("missing_rust", [False, True])
def test_build_without_rust_tui_injects_python_dsns_without_rust_sources(
    release_tree: Path, missing_rust: bool
) -> None:
    if missing_rust:
        (release_tree / _RUST_TARGET).unlink()
    else:
        (release_tree / _RUST_TARGET).write_text("// No Rust DSN placeholder.\n")

    result = _inject(
        release_tree, _CLI_DSN, _ACP_DSN, required=True, skip_rust_tui=True
    )

    assert result.returncode == 0, result.stderr
    python_values = runpy.run_path(str(release_tree / _PYTHON_TARGET))
    assert python_values["_CLI_SENTRY_DSN"] == _CLI_DSN
    assert python_values["_ACP_SENTRY_DSN"] == _ACP_DSN
    assert "continuing without the Rust terminal" in result.stderr
