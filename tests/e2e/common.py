from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Protocol
import weakref

import pexpect
import pyte

from vibe.app_server.run_export import EXPORT_FILENAME, RunExport


class SpawnedVibeProcessFixture(Protocol):
    def __call__(
        self, workdir: Path, extra_args: Sequence[str] | None = None
    ) -> AbstractContextManager[tuple[pexpect.spawn, io.StringIO]]: ...


VIBE_EXECUTABLE = str(Path(sys.executable).with_name("vibe"))
# The Rust CLI a test runs, set by the `rust_cli` fixture.
RUST_CLI_ENV = "VIBE_E2E_RUST_CLI"


def run_rust_vibe(
    cwd: Path, args: Sequence[str], *, timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    """Run the Rust CLI in `cwd` to completion, on this checkout's app server."""
    env = os.environ.copy()
    executable = env.pop(RUST_CLI_ENV)
    env["VIBE_APP_SERVER_BIN"] = str(Path(sys.executable).with_name("vibe-app-server"))
    for name in ("VIBE_APP_SERVER_CMD", "VIBE_APP_SERVER_CWD", "VIBE_CLI"):
        env.pop(name, None)
    return subprocess.run(
        [executable, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        check=False,
    )


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
    # The TUI may draw a gap with a cursor move instead of a space character.
    return re.compile(
        ansi.join(" ?" if char == " " else re.escape(char) for char in text)
    )


# The size `spawned_vibe_process` gives the child terminal.
TERMINAL_ROWS, TERMINAL_COLUMNS = 36, 120


@dataclass
class _Terminal:
    screen: pyte.Screen
    stream: pyte.Stream
    fed: int = 0


_terminals: weakref.WeakKeyDictionary[io.StringIO, _Terminal] = (
    weakref.WeakKeyDictionary()
)


def rendered_screen(captured: io.StringIO) -> str:
    """The child terminal's current screen, as a terminal would display it.

    The TUI redraws only the cells that change, in any order, so text that
    reads as one line on screen is rarely contiguous in the raw output.
    Each capture keeps its terminal and is fed only the output it has not
    seen: re-parsing the whole capture on every poll slows the reader enough
    for the child to block on a full pty.
    """
    terminal = _terminals.get(captured)
    if terminal is None:
        screen = pyte.Screen(TERMINAL_COLUMNS, TERMINAL_ROWS)
        terminal = _terminals[captured] = _Terminal(screen, pyte.Stream(screen))
    output = captured.getvalue()
    terminal.stream.feed(output[terminal.fed :])
    terminal.fed = len(output)
    return "\n".join(line.rstrip() for line in terminal.screen.display)


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
def still_running(pids: Sequence[int], timeout: float = 15.0) -> list[int]:
    """Those of `pids` still running after up to `timeout` seconds; a zombie,
    which only waits for its parent to reap it, has ended.
    """
    deadline = time.monotonic() + timeout
    while (running := [pid for pid in pids if _running(pid)]) and (
        time.monotonic() < deadline
    ):
        time.sleep(0.05)
    return running


def _running(pid: int) -> bool:
    state = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


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


def _rendered_within(
    child: pexpect.spawn, captured: io.StringIO, needle: str, timeout: float
) -> bool:
    """Whether `needle` shows on the rendered screen within `timeout` seconds.

    The TUI redraws only the cells that change, so a text can be on screen
    without ever crossing the output stream whole: match the screen, not the
    stream.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if needle in rendered_screen(captured):
            return True
        try:
            child.expect(r"\S", timeout=0.1)
        except pexpect.TIMEOUT:
            pass
        except pexpect.EOF as exc:
            rendered_tail = strip_ansi(captured.getvalue())[-1200:]
            raise AssertionError(
                f"Child exited while waiting for rendered text: {needle!r}\n\nRendered tail:\n{rendered_tail}"
            ) from exc
    return needle in rendered_screen(captured)


def wait_for_rendered_text(
    child: pexpect.spawn, captured: io.StringIO, needle: str, timeout: float
) -> None:
    if _rendered_within(child, captured, needle, timeout):
        return
    raise AssertionError(
        f"Timed out waiting for rendered text: {needle!r}\n\n"
        f"Rendered screen:\n{rendered_screen(captured)}"
    )


# A tool approval or question ignores keys for this long after it opens, so
# keys typed before it showed cannot answer it (`INPUT_GRACE_PERIOD` in
# vibe/cli-rust/src/question_app/mod.rs).
_INPUT_GRACE_PERIOD_MS = 500
_INPUT_GRACE_PERIOD_ENV = "VIBE_INPUT_GRACE_PERIOD_MS"


def wait_for_dialog_to_accept_keys(
    child: pexpect.spawn, captured: io.StringIO, needle: str, timeout: float = 10
) -> None:
    """Wait until the approval or question showing `needle` takes keys.

    The dialog starts its input grace before drawing its first frame, so the
    grace ends at most one grace period after `needle` shows on screen. Keys
    sent earlier are dropped. The child's output is drained meanwhile.
    """
    wait_for_rendered_text(child, captured, needle=needle, timeout=timeout)
    grace_ms = int(os.environ.get(_INPUT_GRACE_PERIOD_ENV, _INPUT_GRACE_PERIOD_MS))
    deadline = time.monotonic() + grace_ms / 1000
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            child.expect(r"\S", timeout=min(remaining, 0.05))
        except pexpect.TIMEOUT:
            pass


def send_ctrl_c_until_quit_confirmation(
    child: pexpect.spawn, captured: io.StringIO, timeout: float = 3
) -> None:
    """Send Ctrl+C and wait for quit confirmation prompt. Retries if first Ctrl+C interrupts."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        child.sendcontrol("c")
        if _rendered_within(child, captured, "Press Ctrl+C again to quit", 2):
            # Confirmation prompt appeared, send second Ctrl+C
            child.sendcontrol("c")
            return
        # First Ctrl+C may have interrupted something, try again
    raise AssertionError(
        "Timed out waiting for quit confirmation prompt.\n\n"
        f"Rendered screen:\n{rendered_screen(captured)}"
    )
