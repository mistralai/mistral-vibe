#!/usr/bin/env python3
"""Build a project wheel and check cold import module-count budgets.

Generic, config-driven engine. Targets are declared in a TOML file
(default ``<project>/scripts/startup_import_cost.<project>.toml``); each target
may carry an optional ``budget``. Commands without a budget are measured and
reported but never fail the run, so a config can ship budget-free and be filled
in from a baseline run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

_PROJECT_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = _PROJECT_DIR.parent
DEFAULT_PROJECT = _PROJECT_DIR.name
_IMPORTTIME_FIELD_COUNT = 3
_STRACE_MIN_FIELD_COUNT = 5


@dataclass(frozen=True)
class Command:
    label: str
    code: str
    budget: int | None


@dataclass(frozen=True)
class Config:
    commands: list[Command]
    strace_code: str | None
    slowest_count: int


def _run(
    args: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> None:
    subprocess.run(args, cwd=cwd, check=True, env=env)


def _read_pyproject(project: Path) -> dict[str, object]:
    return tomllib.loads((project / "pyproject.toml").read_text())


def _project_name(project: Path) -> str:
    raw = _read_pyproject(project)
    table = raw.get("project")
    name = table.get("name") if isinstance(table, dict) else None
    if not isinstance(name, str):
        raise SystemExit(f"{project}: cannot resolve [project].name")
    return name


def _version(project: Path) -> str:
    raw = _read_pyproject(project)
    table = raw.get("project")
    version = table.get("version") if isinstance(table, dict) else None
    if not isinstance(version, str):
        raise SystemExit(f"{project}: cannot resolve [project].version")
    return version


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _load_config(path: Path) -> Config:
    raw: dict[str, object] = tomllib.loads(path.read_text())

    raw_commands = raw.get("commands")
    if not isinstance(raw_commands, list) or not raw_commands:
        raise SystemExit(f"{path}: 'commands' must be a non-empty list")
    commands: list[Command] = []
    for item in raw_commands:
        if not isinstance(item, dict):
            raise SystemExit(f"{path}: each command must be a table")
        label = item.get("label")
        code = item.get("code")
        if not isinstance(label, str) or not isinstance(code, str):
            raise SystemExit(f"{path}: command missing string 'label' or 'code'")
        commands.append(Command(label, code, _optional_int(item.get("budget"))))

    strace_code = raw.get("strace_code")
    strace_code = strace_code if isinstance(strace_code, str) else None

    slowest = _optional_int(raw.get("slowest_count"))
    return Config(commands, strace_code, slowest if slowest is not None else 15)


def _find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheel found in {dist_dir}")
    if len(wheels) > 1:
        names = ", ".join(w.name for w in wheels)
        raise SystemExit(f"expected one wheel in {dist_dir}, found: {names}")
    return wheels[0]


def _venv_python(root: Path) -> Path:
    _run(["uv", "venv", "--python", sys.executable, str(root)])
    bin_dir = "Scripts" if sys.platform == "win32" else "bin"
    return root / bin_dir / "python"


def _install(python: Path, wheel: Path, project: Path) -> None:
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(python.parent.parent)
    _run(
        [
            "uv",
            "sync",
            "--frozen",
            "--no-dev",
            "--no-install-project",
            "--directory",
            str(project),
        ],
        env=env,
    )
    _run([
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "--compile-bytecode",
        str(wheel),
    ])


def _parse_importtime(stderr: str) -> tuple[int, list[tuple[int, str]]]:
    modules: list[tuple[int, str]] = []
    for line in stderr.splitlines():
        if not line.startswith("import time:"):
            continue
        parts = line[len("import time:") :].split("|")
        if len(parts) != _IMPORTTIME_FIELD_COUNT:
            continue
        self_field = parts[0].strip()
        if self_field == "self [us]":
            continue
        try:
            self_us = int(self_field)
        except ValueError:
            continue
        modules.append((self_us, parts[2].strip()))

    modules.sort(key=lambda item: item[0], reverse=True)
    return len(modules), modules


def _measurement_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _measure_importtime(
    python: Path, code: str
) -> tuple[float, int, list[tuple[int, str]]]:
    start = time.perf_counter()
    result = subprocess.run(
        [str(python), "-X", "importtime", "-c", code],
        cwd=python.parent.parent,
        capture_output=True,
        text=True,
        env=_measurement_env(),
    )
    wall = time.perf_counter() - start
    if result.returncode != 0:
        print(result.stderr.rstrip(), file=sys.stderr)
        raise SystemExit(f"import failed (exit {result.returncode}): {code!r}")
    module_count, modules = _parse_importtime(result.stderr)
    return wall, module_count, modules


def _strace_total_calls(summary: str) -> int | None:
    for line in summary.splitlines():
        fields = line.split()
        if not fields or fields[-1] != "total":
            continue
        if len(fields) < _STRACE_MIN_FIELD_COUNT:
            continue
        # Columns: %time, seconds, usecs/call, calls, [errors], total.
        # strace always prints usecs/call on the total line, so calls is index 3.
        calls_field = fields[3]
        if not calls_field.isdigit():
            continue
        return int(calls_field)
    return None


def _measure_strace(python: Path, code: str) -> None:
    if not sys.platform.startswith("linux") or shutil.which("strace") is None:
        print("file operations: strace unavailable - skipped")
        return

    result = subprocess.run(
        ["strace", "-f", "-c", "-e", "trace=%file", str(python), "-c", code],
        cwd=python.parent.parent,
        capture_output=True,
        text=True,
        env=_measurement_env(),
    )
    if result.returncode != 0:
        print(f"file operations: strace failed (exit {result.returncode}) - skipped")
        if result.stderr:
            print(result.stderr.rstrip())
        return
    total = _strace_total_calls(result.stderr)
    if total is not None:
        print(f"file operations (strace, {code!r}): {total} calls")
    else:
        print(f"file operations (strace, {code!r}): see summary below")
    print(result.stderr.rstrip())


def _report_command(command: Command, python: Path, slowest_count: int) -> int:
    wall, module_count, modules = _measure_importtime(python, command.code)
    budget = command.budget
    print(f"command: {command.label}")
    print(f"  wall time: {wall:.3f} s")
    if budget is not None:
        print(f"  imported modules: {module_count} / {budget}")
    else:
        print(f"  imported modules: {module_count} (no budget)")
    print(f"  slowest {slowest_count} modules (self time):")
    for self_us, name in modules[:slowest_count]:
        print(f"    {self_us:>10} us  {name}")
    print()
    return module_count


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project", default=DEFAULT_PROJECT, help="project dir under repo root"
    )
    parser.add_argument(
        "--dist-name",
        default=None,
        help="distribution name; defaults to [project].name in the project pyproject.toml",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="config TOML; defaults to <project>/scripts/startup_import_cost.<project>.toml",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    project = REPO_ROOT / args.project
    if not project.is_dir():
        raise SystemExit(f"project dir not found: {project}")
    dist_name = args.dist_name or _project_name(project)
    config_path = (
        Path(args.config)
        if args.config
        else project / "scripts" / f"startup_import_cost.{project.name}.toml"
    )
    if not config_path.is_file():
        raise SystemExit(f"config not found: {config_path}")
    config = _load_config(config_path)

    version = _version(project)
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="vibe-startup-import-cost.") as tmp:
        tmp_path = Path(tmp)
        dist_dir = tmp_path / "dist"
        dist_dir.mkdir()

        _run([
            "uv",
            "build",
            "--directory",
            str(project),
            "--wheel",
            "--out-dir",
            str(dist_dir),
        ])

        wheel = _find_wheel(dist_dir)
        python = _venv_python(tmp_path / "env")
        _install(python, wheel, project)

        print(f"=== startup import cost ({dist_name} {version}) ===\n")
        for command in config.commands:
            module_count = _report_command(command, python, config.slowest_count)
            if command.budget is not None and module_count > command.budget:
                failures.append(
                    f"{command.label}: {module_count} modules exceeds budget {command.budget}"
                )

        if config.strace_code is not None:
            _measure_strace(python, config.strace_code)

        size = wheel.stat().st_size
        print(f"installed wheel size: {size} bytes ({size / 1024:.1f} KiB)")

    if failures:
        raise SystemExit(
            "Import budget exceeded:\n"
            + "\n".join(f"- {failure}" for failure in failures)
        )


if __name__ == "__main__":
    main()
