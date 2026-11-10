"""Discover and load declarative Vibe client scenarios."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import importlib.util
import os
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any, Protocol, runtime_checkable

from e2e.app_server.config import SCENARIO_DIR

AppServerEvent = dict[str, Any]
Release = dict[str, int]
Resize = dict[str, tuple[int, int]]
SnapshotMarker = dict[str, str | None]
Timeline = list[str | AppServerEvent | Release | Resize | SnapshotMarker]
_RESIZE_DIMENSIONS = 2
_REMOVED_ATTRIBUTES = ("capture_startup", "capture_steps")
ASSERTS_NOTHING = "asserts nothing: add take_snapshot() or an expectation"


def resize(rows: int, columns: int) -> Resize:
    """Resize the client's PTY before capturing the next settled frame."""
    return {"resize": (rows, columns)}


def take_snapshot(label: str | None = None) -> SnapshotMarker:
    """Store a golden SVG of the frame settled by the preceding step (or startup)."""
    return {"take_snapshot": label}


# Kitty F23: the client advances its frozen replay clock 100 ms per press.
_ADVANCE_KEY = "\x1b[57386u"
_ADVANCE_STEP_MS = 100


def advance(ms: int) -> str:
    """Let ``ms`` of UI time pass (a multiple of 100); replay time never moves otherwise."""
    if ms <= 0 or ms % _ADVANCE_STEP_MS:
        raise ValueError("advance takes a positive multiple of 100 ms")
    return _ADVANCE_KEY * (ms // _ADVANCE_STEP_MS)


@dataclass(frozen=True)
class Action:
    """One external action; a pattern ``url`` matches a generated path in full."""

    kind: str
    url: str | re.Pattern[str]

    def matches(self, actual: Action) -> bool:
        if self.kind != actual.kind or not isinstance(actual.url, str):
            return False
        if isinstance(self.url, re.Pattern):
            return self.url.fullmatch(actual.url) is not None
        return self.url == actual.url


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
    # Events gated on settles after this step's input, one event per settle.
    release_after: int = 0
    resize: tuple[int, int] | None = None
    snapshot: str | None = None
    """Label of the golden SVG taken once this step settles, if any."""


@dataclass
class Scenario:
    """One loaded Vibe client scenario."""

    name: str
    steps: list[Step]
    startup_snapshot: str | None = None
    """Label of the golden SVG taken once startup settles, if any."""
    env: Mapping[str, str] = field(default_factory=dict)
    handshake: Mapping[str, Any] = field(default_factory=dict)
    on_request: Mapping[str, list[AppServerEvent]] = field(default_factory=dict)
    """Events the server would emit while answering a request, by method."""
    request_methods: frozenset[str] = frozenset()
    """Additional client requests this scenario pins in its golden."""
    expected_actions: list[Action] | None = None
    expected_clipboard: str | None = None
    expected_titles: tuple[str, ...] | None = None
    clipboard_contains: tuple[str, ...] = ()
    clipboard_excludes: tuple[str, ...] = ()
    screen_contains: tuple[str, ...] = ()
    screen_excludes: tuple[str, ...] = ()
    screen_rows: Mapping[int, str] = field(default_factory=dict)
    screen_cursor: tuple[int, int] | None = None
    """Expected final terminal cursor ``(row, column)``."""
    screen_hyperlinks: tuple[tuple[str, str], ...] | None = None
    """Exact OSC 8 runs ``(text, target)`` on the final screen, top to bottom."""
    mouse_tracking: bool | None = None
    """Whether the terminal must end up reporting mouse events to the client."""
    mouse_tracking_seen: bool | None = None
    """Whether mouse reports must have been turned on at any point of the run."""
    skip_reason: str | None = None
    exit_after_last_step: bool = False
    settle_per_key: bool = False
    """Settle after every keypress, for scenarios needing each key in its own read."""
    terminal_responses: Mapping[str, str] = field(default_factory=dict)
    """Terminal output queries and their input replies."""
    exit_responses: Mapping[str, str] = field(default_factory=dict)
    """Post-exit output triggers and their input replies, FIFO."""
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

    def takes_snapshots(self) -> bool:
        return self.startup_snapshot is not None or any(
            step.snapshot is not None for step in self.steps
        )

    def has_expectations(self) -> bool:
        """Whether the scenario asserts anything besides its golden requests."""
        return self.takes_snapshots() or any((
            self.request_methods,
            self.expected_actions is not None,
            self.expected_clipboard is not None,
            self.expected_titles is not None,
            self.clipboard_contains,
            self.clipboard_excludes,
            self.screen_contains,
            self.screen_excludes,
            self.screen_rows,
            self.screen_cursor is not None,
            self.screen_hyperlinks is not None,
            self.mouse_tracking is not None,
            self.mouse_tracking_seen is not None,
        ))


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
    if removed := [attr for attr in _REMOVED_ATTRIBUTES if hasattr(module, attr)]:
        raise TypeError(
            f"{origin}: {removed} is gone; mark frames with take_snapshot()"
        )
    startup_snapshot, steps = _split_timeline(module.timeline)
    exits = getattr(module, "exit_after_last_step", False)
    if exits and steps and steps[-1].snapshot is not None:
        raise ValueError(f"{origin}: the exiting last step cannot take a snapshot")
    return Scenario(
        name=name,
        steps=steps,
        startup_snapshot=startup_snapshot,
        env=getattr(module, "env", {}),
        handshake=getattr(module, "handshake", {}),
        on_request=getattr(module, "on_request", {}),
        request_methods=frozenset(getattr(module, "request_methods", ())),
        expected_actions=getattr(module, "expected_actions", None),
        expected_clipboard=getattr(module, "expected_clipboard", None),
        expected_titles=getattr(module, "expected_titles", None),
        clipboard_contains=tuple(getattr(module, "clipboard_contains", ())),
        clipboard_excludes=tuple(getattr(module, "clipboard_excludes", ())),
        screen_contains=_texts(module, "screen_contains"),
        screen_excludes=_texts(module, "screen_excludes"),
        screen_rows=getattr(module, "screen_rows", {}),
        screen_cursor=getattr(module, "screen_cursor", None),
        screen_hyperlinks=getattr(module, "screen_hyperlinks", None),
        mouse_tracking=getattr(module, "mouse_tracking", None),
        mouse_tracking_seen=getattr(module, "mouse_tracking_seen", None),
        skip_reason=getattr(module, "skip", None),
        exit_after_last_step=exits,
        settle_per_key=getattr(module, "settle_per_key", False),
        terminal_responses=getattr(module, "terminal_responses", {}),
        exit_responses=getattr(module, "exit_responses", {}),
        client_args=tuple(getattr(module, "client_args", ())),
        stdout_before_completion=tuple(getattr(module, "stdout_before_completion", ())),
        headless_returncode=getattr(module, "headless_returncode", 0),
        headless_stderr=getattr(module, "headless_stderr", None),
        headless_requests=tuple(getattr(module, "headless_requests", ())),
    )


def _texts(module: ModuleType, name: str) -> tuple[str, ...]:
    texts = getattr(module, name, ())
    if isinstance(texts, str) or not all(isinstance(text, str) for text in texts):
        raise TypeError(f"{module.__name__}.{name} must be a sequence of strings")
    return tuple(texts)


def _split_timeline(timeline: Timeline) -> tuple[str | None, list[Step]]:
    """Split a timeline into input steps and the optional startup snapshot label."""
    startup_snapshot: str | None = None
    steps: list[Step] = []
    for item in timeline:
        if isinstance(item, str):
            steps.append(Step(item))
            continue
        if "take_snapshot" in item:
            label = item["take_snapshot"]
            if not steps:
                if startup_snapshot is not None:
                    raise ValueError("startup is already snapshotted")
                startup_snapshot = label or "startup"
                continue
            if steps[-1].snapshot is not None:
                raise ValueError(f"step {len(steps) - 1} is already snapshotted")
            steps[-1].snapshot = label or f"step{len(steps) - 1}"
            continue
        starts_step = "release" in item or "resize" in item
        if steps and steps[-1].snapshot is not None and not starts_step:
            raise ValueError("take_snapshot() must follow every event of its step")
        if "release" in item:
            release = item["release"]
            if not isinstance(release, int) or release < 1:
                raise ValueError("a replay release must be a positive integer")
            steps.append(Step("", release=release))
            continue
        if "release_after" in item:
            release_after = item["release_after"]
            if not isinstance(release_after, int) or release_after < 1:
                raise ValueError("a replay release must be a positive integer")
            if not steps:
                raise ValueError("timeline starts with a release before any input")
            steps[-1].release_after = release_after
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
    return startup_snapshot, steps
