from __future__ import annotations

import pytest

from vibe.core.config import MCPStdio
import vibe.core.config.models as config_models


@pytest.mark.parametrize(
    "command",
    [
        r"C:\Program Files\some-tool\tool.exe",
        r"C:\tools\server.exe",
        r"\\server\share\server.exe",
    ],
)
def test_windows_mcp_command_preserves_absolute_native_path(
    monkeypatch, command
) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: True)
    config = MCPStdio(name="test", transport="stdio", command=command)

    assert config.argv() == [command]


def test_windows_mcp_command_appends_args_unchanged(monkeypatch) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: True)
    command = r"C:\Program Files\some-tool\tool.exe"
    args = ["--stdio", "value with space"]
    config = MCPStdio(name="test", transport="stdio", command=command, args=args)

    assert config.argv() == [command, *args]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (r"C:tools\server.exe", [r"C:toolsserver.exe"]),
        ("python -m server --port 8080", ["python", "-m", "server", "--port", "8080"]),
        ("C:/tools/server.exe --stdio", ["C:/tools/server.exe", "--stdio"]),
        ("", []),
    ],
)
def test_mcp_stdio_argv_preserves_historical_string_commands(
    monkeypatch, command, expected
) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: True)
    config = MCPStdio(name="test", transport="stdio", command=command)

    assert config.argv() == expected


def test_mcp_stdio_argv_preserves_explicit_list_and_args(monkeypatch) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: True)
    command = ["python", "-m", "server"]
    args = ["--port", "8080"]
    config = MCPStdio(name="test", transport="stdio", command=command, args=args)

    assert config.argv() == [*command, *args]


def test_mcp_stdio_argv_preserves_non_windows_behavior(monkeypatch) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: False)
    command = r"C:\Program Files\some-tool\tool.exe"
    config = MCPStdio(name="test", transport="stdio", command=command)

    assert config.argv() == ["C:Program", "Filessome-tooltool.exe"]


@pytest.mark.parametrize(
    ("windows", "command", "args"),
    [
        (True, r"C:\Program Files\some-tool\tool.exe", []),
        (True, r"C:\tools\server.exe", []),
        (True, r"\\server\share\server.exe", []),
        (True, r"C:\Program Files\some-tool\tool.exe", ["--stdio", "value with space"]),
        (True, r"C:tools\server.exe", []),
        (True, "python -m server --port 8080", []),
        (True, ["python", "-m", "server"], ["--port", "8080"]),
        (False, r"C:\Program Files\some-tool\tool.exe", []),
        (True, "", []),
    ],
)
def test_mcp_stdio_argv_is_idempotent_across_reloads(
    monkeypatch, windows, command, args
) -> None:
    monkeypatch.setattr(config_models, "is_windows", lambda: windows)
    data = {"name": "test", "transport": "stdio", "command": command, "args": args}
    first = MCPStdio.model_validate(data)
    serialized = first.model_dump()
    argv = first.argv()

    assert first.argv() == argv
    assert first.model_dump() == serialized

    reloaded = MCPStdio.model_validate(serialized)

    assert reloaded.command == first.command
    assert reloaded.args == first.args
    assert reloaded.argv() == argv
    assert reloaded.model_dump() == serialized
