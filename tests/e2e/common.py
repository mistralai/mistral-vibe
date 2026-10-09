from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Protocol

import pexpect

from vibe.app_server.run_export import EXPORT_FILENAME, RunExport


class SpawnedVibeProcessFixture(Protocol):
    def __call__(
        self, workdir: Path, extra_args: Sequence[str] | None = None
    ) -> AbstractContextManager[tuple[pexpect.spawn, io.StringIO]]: ...


VIBE_EXECUTABLE = str(Path(sys.executable).with_name("vibe"))


def run_vibe_headless(
    workdir: Path, args: Sequence[str], *, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run the installed `vibe` in `workdir` to completion, as a caller would."""
    return subprocess.run(
        [VIBE_EXECUTABLE, "--workdir", str(workdir), *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
        check=False,
    )


def read_export(output_dir: Path) -> RunExport:
    return RunExport.model_validate_json(
        (output_dir / EXPORT_FILENAME).read_text(encoding="utf-8")
    )


def export_config(export: RunExport) -> dict[str, Any]:
    """The effective config the export records, which a started run always has."""
    assert export.config is not None, "the export records no effective config"
    return export.config


def read_export_config(output_dir: Path) -> dict[str, Any]:
    return export_config(read_export(output_dir))


def ansi_tolerant_pattern(text: str) -> re.Pattern[str]:
    ansi = r"(?:\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r|\n)*"
    return re.compile(ansi.join(re.escape(char) for char in text))


def write_e2e_config(
    vibe_home: Path,
    api_base: str,
    *,
    provider_name: str = "mock-provider",
    backend: str = "generic",
    settings: Sequence[str] = (),
    model_settings: Sequence[str] = (),
) -> None:
    """Write a config routing `mock-model` to `api_base`.

    `settings` are extra top-level TOML lines; `model_settings` are extra lines
    of the `mock-model` table.
    """
    vibe_home.mkdir(parents=True, exist_ok=True)
    (vibe_home / "config.toml").write_text(
        "\n".join([
            'active_model = "mock-model"',
            "enable_update_checks = false",
            "disable_welcome_banner_animation = true",
            *settings,
            "",
            "[[providers]]",
            f'name = "{provider_name}"',
            f'api_base = "{api_base}"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            f'backend = "{backend}"',
            "",
            "[[models]]",
            'name = "mock-model"',
            f'provider = "{provider_name}"',
            'alias = "mock-model"',
            *model_settings,
        ]),
        encoding="utf-8",
    )


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", "", text)


def poll_until(predicate: Callable[[], bool], timeout: float, message: str) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(message)


# Waiting on the backend must always drain the child, never just sleep on the
# predicate. Textual's writer thread has a 30-slot queue and blocks on a full one, so
# a pty nobody reads eventually stalls the app's event loop: it stops handling input,
# never dispatches the turn, and the request the caller is waiting for never arrives.
# The startup burst alone is ~26KB against a 64KB Linux pty buffer.
def wait_for_request_count_while_draining_child_output(
    child: pexpect.spawn,
    captured: io.StringIO,
    request_count_getter: Callable[[], int],
    *,
    expected_count: int,
    timeout: float,
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if request_count_getter() >= expected_count:
            return
        try:
            child.expect(r"\S", timeout=0.05)
        except pexpect.TIMEOUT:
            pass
    rendered_tail = strip_ansi(captured.getvalue())[-1200:]
    raise AssertionError(
        f"Timed out waiting for {expected_count} backend request(s).\n\n"
        f"Rendered tail:\n{rendered_tail}"
    )


def wait_for_main_screen(child: pexpect.spawn, timeout: float = 20.0) -> None:
    child.expect(ansi_tolerant_pattern("Mistral Vibe v"), timeout=timeout)


def wait_for_rendered_text(
    child: pexpect.spawn, captured: io.StringIO, needle: str, timeout: float
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if needle in strip_ansi(captured.getvalue()):
            return
        try:
            child.expect(r"\S", timeout=0.1)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF as exc:
            rendered_tail = strip_ansi(captured.getvalue())[-1200:]
            raise AssertionError(
                f"Child exited while waiting for rendered text: {needle!r}\n\nRendered tail:\n{rendered_tail}"
            ) from exc
    rendered_tail = strip_ansi(captured.getvalue())[-1200:]
    raise AssertionError(
        f"Timed out waiting for rendered text: {needle!r}\n\nRendered tail:\n{rendered_tail}"
    )


def send_ctrl_c_until_quit_confirmation(
    child: pexpect.spawn, captured: io.StringIO, timeout: float = 3
) -> None:
    """Send Ctrl+C and wait for quit confirmation prompt. Retries if first Ctrl+C interrupts."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        child.sendcontrol("c")
        try:
            child.expect(ansi_tolerant_pattern("Press Ctrl+C again to quit"), timeout=2)
            # Confirmation prompt appeared, send second Ctrl+C
            child.sendcontrol("c")
            return
        except pexpect.TIMEOUT:
            # First Ctrl+C may have interrupted something, try again
            continue
    rendered_tail = strip_ansi(captured.getvalue())[-1200:]
    raise AssertionError(
        f"Timed out waiting for quit confirmation prompt.\n\nRendered tail:\n{rendered_tail}"
    )
