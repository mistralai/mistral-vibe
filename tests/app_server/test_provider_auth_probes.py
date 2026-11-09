"""The ``/status`` source probes: file and keyring inspection, deadlines,
permits, and abandonment.

Split from ``test_provider_auth.py`` to keep that file under the 1000-line
mark; the view and rendering behavior live there.
"""

from __future__ import annotations

from collections.abc import Iterator
import logging
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from tests.conftest import build_test_vibe_config
import vibe.app_server._provider_auth as provider_auth_module
from vibe.app_server._provider_auth import (
    SourceProbe,
    probe_env_file,
    read_provider_auth,
)
from vibe.app_server.provider_auth import (
    ProviderCredentialSourceKind,
    ProviderCredentialSourceState,
)
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend

_API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"


def _kinds(view) -> list[tuple[ProviderCredentialSourceKind, str]]:
    rendered = []
    for source in view.sources:
        marker = (
            "in_use"
            if source.state is ProviderCredentialSourceState.IN_USE
            else "unavailable"
            if source.state is ProviderCredentialSourceState.UNAVAILABLE
            else "present"
        )
        rendered.append((source.kind, marker))
    return rendered


@pytest.fixture(autouse=True)
def _fresh_probe_permits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets its own permits.

    A test that abandons a hanging probe holds its permit until the probe
    answers, and that process-global hold must not decide the next test's
    verdict.
    """
    monkeypatch.setattr(
        provider_auth_module, "_PROBE_PERMITS", threading.BoundedSemaphore(4)
    )


@pytest.fixture
def release_hanging_probe() -> Iterator[threading.Event]:
    """Hanging probes wait on this event instead of sleeping: teardown
    releases the thread at once instead of leaving it blocked for seconds.
    """
    event = threading.Event()
    yield event
    event.set()


def _api_key_config(
    *,
    api_base: str = "https://api.anthropic.com/v1",
    display_name: str | None = "Claude Sonnet",
) -> VibeConfigSchema:
    provider = ProviderConfig(
        name="anthropic",
        api_base=api_base,
        api_key_env_var=_API_KEY_ENV_VAR,
        backend=Backend.GENERIC,
    )
    model = ModelConfig(
        name="claude-sonnet",
        provider="anthropic",
        alias="claude-sonnet",
        display_name=display_name,
    )
    return build_test_vibe_config(
        providers=[provider], models=[model], active_model="claude-sonnet"
    )


class TestProbeDeadline:
    @pytest.mark.asyncio
    async def test_hanging_source_is_unavailable_within_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch, release_hanging_probe: threading.Event
    ) -> None:
        monkeypatch.delenv(_API_KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(provider_auth_module, "_SOURCE_DEADLINE_SECONDS", 0.1)
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)

        def _hang(var: str) -> SourceProbe:
            release_hanging_probe.wait()
            return SourceProbe(value="late")

        monkeypatch.setattr(provider_auth_module, "probe_keyring", _hang)
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        started = time.monotonic()
        view = await read_provider_auth(_api_key_config())
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        assert _kinds(view) == [
            (ProviderCredentialSourceKind.OS_KEYRING, "unavailable")
        ]

    @pytest.mark.asyncio
    async def test_real_deadline_keeps_the_read_under_two_seconds(
        self, monkeypatch: pytest.MonkeyPatch, release_hanging_probe: threading.Event
    ) -> None:
        """The shipped 1s deadline, not a patched one: a source hanging past it
        still leaves the read inside the contract's two-second bound.
        """
        monkeypatch.delenv(_API_KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)

        def _hang(var: str) -> SourceProbe:
            release_hanging_probe.wait()
            return SourceProbe(value="late")

        monkeypatch.setattr(provider_auth_module, "probe_keyring", _hang)
        monkeypatch.setattr(
            provider_auth_module,
            "probe_env_file",
            lambda _path, _var: SourceProbe(value=None),
        )

        started = time.monotonic()
        view = await read_provider_auth(_api_key_config())
        elapsed = time.monotonic() - started

        assert elapsed < 2.0
        assert _kinds(view) == [
            (ProviderCredentialSourceKind.OS_KEYRING, "unavailable")
        ]

    @pytest.mark.asyncio
    async def test_both_sources_hanging_stay_within_one_deadline(
        self, monkeypatch: pytest.MonkeyPatch, release_hanging_probe: threading.Event
    ) -> None:
        """The two probes run concurrently, so both hanging costs one deadline,
        not two.
        """
        monkeypatch.delenv(_API_KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(provider_auth_module, "_SOURCE_DEADLINE_SECONDS", 0.1)
        monkeypatch.setattr(provider_auth_module, "env_value_origin", lambda _key: None)

        def _hang(*args: object) -> SourceProbe:
            release_hanging_probe.wait()
            return SourceProbe(value="late")

        monkeypatch.setattr(provider_auth_module, "probe_keyring", _hang)
        monkeypatch.setattr(provider_auth_module, "probe_env_file", _hang)

        started = time.monotonic()
        view = await read_provider_auth(_api_key_config())
        elapsed = time.monotonic() - started

        assert elapsed < 1.0
        assert _kinds(view) == [
            (ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE, "unavailable"),
            (ProviderCredentialSourceKind.OS_KEYRING, "unavailable"),
        ]

    @pytest.mark.asyncio
    async def test_exhausted_permits_report_unavailable_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The permit pool bounds the leak: once every permit is held by a
        wedged probe, a further probe does not wait for its deadline — it
        reports the source as unavailable at once.
        """
        monkeypatch.setattr(
            provider_auth_module, "_PROBE_PERMITS", threading.BoundedSemaphore(0)
        )

        started = time.monotonic()
        probe = await provider_auth_module._probe_with_deadline(
            lambda: SourceProbe(value="never-read")
        )
        elapsed = time.monotonic() - started

        assert probe.unavailable
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_probe_failure_log_carries_no_storage_error_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A storage error's text is not ours to republish: the log gets the
        exception type name only, never the message a backend may fill with
        credential material.
        """

        def _explode() -> SourceProbe:
            raise RuntimeError("the password is hunter2")

        with caplog.at_level(logging.DEBUG, logger="vibe"):
            probe = await provider_auth_module._probe_with_deadline(_explode)

        assert probe.unavailable
        assert "RuntimeError" in caplog.text
        assert "hunter2" not in caplog.text

    def test_an_abandoned_probe_does_not_block_interpreter_exit(self) -> None:
        """A wedged source (a blocking FIFO, a stuck OS keychain) must not
        outlive the user's quit: the abandoned probe thread is a daemon, so
        interpreter shutdown does not join it.
        """
        script = textwrap.dedent(
            """
            import asyncio
            import threading

            from vibe.app_server._provider_auth import _probe_with_deadline

            async def main() -> None:
                started = threading.Event()

                def _wedged() -> object:
                    started.set()
                    threading.Event().wait()

                await _probe_with_deadline(_wedged)
                started.wait(timeout=5)

            asyncio.run(main())
            print("exited")
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=10
        )

        assert result.returncode == 0, result.stderr
        assert "exited" in result.stdout


class TestProbeEnvFile:
    def test_missing_file_is_absent(self, tmp_path: Path) -> None:
        assert probe_env_file(
            tmp_path / "missing.env", _API_KEY_ENV_VAR
        ) == SourceProbe(value=None)

    def test_present_value(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(f"{_API_KEY_ENV_VAR}=file-key\n", encoding="utf-8")

        assert probe_env_file(env_file, _API_KEY_ENV_VAR) == SourceProbe(
            value="file-key"
        )

    def test_non_utf8_file_is_unavailable(self, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_bytes(b"\xff\xfe\x00MISTRAL")

        assert probe_env_file(env_file, _API_KEY_ENV_VAR).unavailable

    def test_fifo_path_is_inspected(self, tmp_path: Path) -> None:
        """A FIFO is a supported ``.env`` shape (1Password-style environment
        managers): the probe reads it rather than reporting the file absent.
        A writer thread keeps the read from blocking on the pipe.
        """
        fifo_path = tmp_path / ".env"
        os.mkfifo(fifo_path)

        def _write() -> None:
            with open(fifo_path, "w") as fifo:
                fifo.write(f"{_API_KEY_ENV_VAR}=fifo-key\n")

        writer = threading.Thread(target=_write)
        writer.start()
        try:
            probe = probe_env_file(fifo_path, _API_KEY_ENV_VAR)
        finally:
            writer.join()

        assert probe == SourceProbe(value="fifo-key")

    def test_empty_value_entry_is_checked_and_empty(self, tmp_path: Path) -> None:
        # `KEY=` holds no credential, same as a missing entry.
        env_file = tmp_path / ".env"
        env_file.write_text(f"{_API_KEY_ENV_VAR}=\n", encoding="utf-8")

        assert probe_env_file(env_file, _API_KEY_ENV_VAR) == SourceProbe(value=None)

    def test_unreadable_file_is_unavailable(self, tmp_path: Path) -> None:
        """A file this process cannot read is an inspection failure, not a
        checked-and-empty source. The permission bit only bites for
        unprivileged processes — CI agents run as root and still read
        chmod-000 files — so the test skips where it cannot make the file
        unreadable.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(f"{_API_KEY_ENV_VAR}=file-key\n", encoding="utf-8")
        env_file.chmod(0o000)
        try:
            if os.access(env_file, os.R_OK):
                pytest.skip("this process reads chmod-000 files (running as root)")
            probe = probe_env_file(env_file, _API_KEY_ENV_VAR)
        finally:
            env_file.chmod(0o644)

        assert probe.unavailable
