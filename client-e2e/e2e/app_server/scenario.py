"""Discover and load declarative Vibe client scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import sys
from types import ModuleType
from typing import Any, Protocol, runtime_checkable

from e2e.app_server.config import SCENARIO_DIR

AppServerEvent = dict[str, Any]
Release = dict[str, int]
Resize = dict[str, tuple[int, int]]
Timeline = list[str | AppServerEvent | Release | Resize]
_RESIZE_DIMENSIONS = 2


def resize(rows: int, columns: int) -> Resize:
    """Resize the client's PTY before capturing the next settled frame."""
    return {"resize": (rows, columns)}


@dataclass(frozen=True)
class Action:
    kind: str
    url: str


@dataclass(frozen=True)
class Request:
    """One semantic client-to-server JSON-RPC request."""

    method: str
    params: Mapping[str, Any]


@runtime_checkable
class ScenarioModule(Protocol):
    """The required surface of a scenario module."""

    timeline: Timeline


@dataclass
class Step:
    """One terminal input followed by the app-server events it releases."""

    text: str
    events: list[AppServerEvent] = field(default_factory=list)
    release: int = 0
    resize: tuple[int, int] | None = None


@dataclass
class Scenario:
    """One loaded Vibe client scenario."""

    name: str
    steps: list[Step]
    env: Mapping[str, str] = field(default_factory=dict)
    handshake: Mapping[str, Any] = field(default_factory=dict)
    on_request: Mapping[str, list[AppServerEvent]] = field(default_factory=dict)
    """Events the server would emit while answering a request, by method."""
    request_methods: frozenset[str] = frozenset()
    """Additional client requests this scenario compares for parity."""
    expected_actions: Mapping[str, list[Action]] = field(default_factory=dict)
    expected_clipboard: str | None = None
    expected_titles: tuple[str, ...] | None = None
    clipboard_contains: tuple[str, ...] = ()
    clipboard_excludes: tuple[str, ...] = ()
    clipboard_clients: frozenset[str] | None = None
    screen_contains: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    screen_excludes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    screen_rows: Mapping[str, Mapping[int, str]] = field(default_factory=dict)
    skip_reason: str | None = None
    capture_startup: bool = True
    capture_steps: set[int] | None = None
    exit_after_last_step: bool = False
    settle_per_key: bool = False
    """Settle after every keypress, for scenarios needing each key in its own read."""
    terminal_responses: Mapping[str, str] = field(default_factory=dict)
    """Terminal output queries and their input replies."""
    client_args: tuple[str, ...] = ()
    """Extra CLI args appended to the client command (e.g. `--continue`)."""
    stdout_before_completion: tuple[Mapping[str, Any], ...] = ()
    """Headless JSONL expected before the replay releases `turn/completed`."""
    headless_returncode: int = 0
    """Headless expected exit code (non-zero for error paths like the limit)."""
    headless_stderr: str | None = None
    """Headless expected exact stderr text (bare, no `Error:` prefix)."""
    headless_requests: tuple[str, ...] = ()
    """Client-to-server methods a headless run must have sent before exiting."""

    def event_batches(self) -> list[list[AppServerEvent]]:
        """Return non-empty server-event batches in input order."""
        return [step.events for step in self.steps if step.events]


def available_scenarios() -> list[str]:
    """Return every public terminal scenario path without its Python suffix."""
    return [name for name in _available_scenarios() if not name.startswith("headless/")]


def available_headless_scenarios() -> list[str]:
    """Return every public headless scenario path without its Python suffix."""
    return [name for name in _available_scenarios() if name.startswith("headless/")]


def _available_scenarios() -> list[str]:
    if not SCENARIO_DIR.is_dir():
        return []
    paths = (
        path.relative_to(SCENARIO_DIR).with_suffix("")
        for path in SCENARIO_DIR.rglob("*.py")
        if not any(
            part.startswith("_") for part in path.relative_to(SCENARIO_DIR).parts
        )
    )
    return sorted(path.as_posix() for path in paths)


def load_scenario(name: str) -> Scenario:
    """Load a named scenario and split its timeline into input steps."""
    path = SCENARIO_DIR / f"{name}.py"
    if not path.is_file():
        available = ", ".join(available_scenarios()) or "(none)"
        raise FileNotFoundError(f"unknown scenario {name!r}; available: {available}")
    module_name = f"scenario_{name.replace('/', '_')}"
    return _build_scenario(_module_from_path(path, module_name), name, os.fspath(path))


def _module_from_path(path: Path, module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load scenario module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_scenario(module: ModuleType, name: str, origin: str) -> Scenario:
    if not isinstance(module, ScenarioModule):
        raise TypeError(f"{origin} must define `timeline`")
    return Scenario(
        name=name,
        steps=_split_timeline(module.timeline),
        env=getattr(module, "env", {}),
        handshake=getattr(module, "handshake", {}),
        on_request=getattr(module, "on_request", {}),
        request_methods=frozenset(getattr(module, "request_methods", ())),
        expected_actions=getattr(module, "expected_actions", {}),
        expected_clipboard=getattr(module, "expected_clipboard", None),
        expected_titles=getattr(module, "expected_titles", None),
        clipboard_contains=tuple(getattr(module, "clipboard_contains", ())),
        clipboard_excludes=tuple(getattr(module, "clipboard_excludes", ())),
        clipboard_clients=(
            frozenset(clients)
            if (clients := getattr(module, "clipboard_clients", None)) is not None
            else None
        ),
        screen_contains=getattr(module, "screen_contains", {}),
        screen_excludes=getattr(module, "screen_excludes", {}),
        screen_rows=getattr(module, "screen_rows", {}),
        skip_reason=getattr(module, "skip", None),
        capture_startup=getattr(module, "capture_startup", True),
        capture_steps=getattr(module, "capture_steps", None),
        exit_after_last_step=getattr(module, "exit_after_last_step", False),
        settle_per_key=getattr(module, "settle_per_key", False),
        terminal_responses=getattr(module, "terminal_responses", {}),
        client_args=tuple(getattr(module, "client_args", ())),
        stdout_before_completion=tuple(getattr(module, "stdout_before_completion", ())),
        headless_returncode=getattr(module, "headless_returncode", 0),
        headless_stderr=getattr(module, "headless_stderr", None),
        headless_requests=tuple(getattr(module, "headless_requests", ())),
    )


def _split_timeline(timeline: Timeline) -> list[Step]:
    steps: list[Step] = []
    for item in timeline:
        if isinstance(item, str):
            steps.append(Step(item))
            continue
        if "release" in item:
            release = item["release"]
            if not isinstance(release, int) or release < 1:
                raise ValueError("a replay release must be a positive integer")
            steps.append(Step("", release=release))
            continue
        if "resize" in item:
            size = item["resize"]
            if (
                not isinstance(size, tuple)
                or len(size) != _RESIZE_DIMENSIONS
                or not all(isinstance(value, int) and value > 0 for value in size)
            ):
                raise ValueError("resize must be a positive (rows, columns) tuple")
            steps.append(Step("", resize=size))
            continue
        if not steps:
            raise ValueError("timeline starts with an event before any input")
        # An `id` marks a server-to-client request (e.g. `callback/call`).
        keys = ("id", "method", "params")
        steps[-1].events.append({key: item[key] for key in keys if key in item})
    return steps
