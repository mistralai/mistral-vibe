from __future__ import annotations

import argparse
import builtins
import sys
import types

import pytest

from vibe.cli.textual_ui.app import serve_textual_ui
from vibe.core.config import SessionLoggingConfig, VibeConfig


@pytest.fixture()
def minimal_config() -> VibeConfig:
    return VibeConfig(session_logging=SessionLoggingConfig(enabled=False))


def test_serve_textual_ui_requires_textual_serve(
    minimal_config: VibeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_import = builtins.__import__

    def raising_import(name: str, *args, **kwargs):  # type: ignore[override]
        if name.startswith("textual_serve"):
            raise ModuleNotFoundError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", raising_import)

    with pytest.raises(RuntimeError) as exc_info:
        serve_textual_ui(minimal_config)

    assert "textual-serve is required" in str(exc_info.value)


def test_serve_textual_ui_invokes_textual_serve(
    minimal_config: VibeConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    class StubServer:
        def __init__(
            self,
            command: str,
            host: str,
            port: int,
            public_url: str | None = None,
            title: str | None = None,
        ) -> None:
            captured.update(
                {
                    "command": command,
                    "host": host,
                    "port": port,
                    "public_url": public_url,
                    "title": title,
                }
            )

        def serve(self, debug: bool = False) -> None:  # noqa: FBT002
            captured["served"] = True

    fake_server_module = types.ModuleType("textual_serve.server")
    setattr(fake_server_module, "Server", StubServer)
    fake_textual_serve = types.ModuleType("textual_serve")
    setattr(fake_textual_serve, "server", fake_server_module)

    monkeypatch.setitem(sys.modules, "textual_serve", fake_textual_serve)
    monkeypatch.setitem(sys.modules, "textual_serve.server", fake_server_module)

    serve_args = argparse.Namespace(
        agent="dev",
        auto_approve=True,
        plan=False,
        enabled_tools=["bash"],
        continue_session=False,
        resume=None,
        initial_prompt="hi there",
    )

    serve_textual_ui(
        minimal_config,
        bind_address="0.0.0.0",
        port=9999,
        public_url="https://example.com",
        serve_args=serve_args,
    )

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
    assert captured["public_url"] == "https://example.com"
    assert captured["served"] is True

    command = str(captured["command"])
    assert "-m vibe.cli.serve_child" in command
    assert "--agent dev" in command
    assert "--enabled-tools bash" in command
    assert "hi there" in command
