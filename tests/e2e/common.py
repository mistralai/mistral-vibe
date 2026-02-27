from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import io
from pathlib import Path
import re
import time
from typing import Protocol

import pexpect


class SpawnedVibeProcessFixture(Protocol):
    def __call__(
        self, workdir: Path
    ) -> AbstractContextManager[tuple[pexpect.spawn, io.StringIO]]: ...


def ansi_tolerant_pattern(text: str) -> re.Pattern[str]:
    ansi = r"(?:\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\r|\n)*"
    return re.compile(ansi.join(re.escape(char) for char in text))


def write_e2e_config(vibe_home: Path, api_base: str) -> None:
    vibe_home.mkdir(parents=True, exist_ok=True)
    (vibe_home / "config.toml").write_text(
        "\n".join([
            'active_model = "mock-model"',
            "enable_update_checks = false",
            "enable_auto_update = false",
            "",
            "[[providers]]",
            'name = "mock-provider"',
            f'api_base = "{api_base}"',
            'api_key_env_var = "MISTRAL_API_KEY"',
            'backend = "generic"',
            "",
            "[[models]]",
            'name = "mock-model"',
            'provider = "mock-provider"',
            'alias = "mock-model"',
        ]),
        encoding="utf-8",
    )


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", "", text)


def wait_for_request_count(
    request_count_getter: Callable[[], int], expected_count: int, timeout: float
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if request_count_getter() >= expected_count:
            return
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {expected_count} backend request(s).")


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
