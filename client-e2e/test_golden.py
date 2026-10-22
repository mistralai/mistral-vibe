"""Assert CLI output matches committed golden SVG snapshots.

A scenario with no golden gets one generated from the current capture and
passes with a warning, so a new scenario is covered from its first run. The
generated golden is uncommitted until reviewed.

On failure, live SVGs are saved under ``goldens/_live/<scenario>/`` next to
the committed goldens so a simple ``diff`` reveals the platform-specific
difference. CI artifact globs already cover ``goldens/_live/**/*.svg`` so the
live SVGs are uploaded automatically — no separate store step needed.
"""

from __future__ import annotations

from dataclasses import replace
import difflib
import json
import os
from pathlib import Path
import re
import tempfile
import warnings
from xml.etree import ElementTree

from e2e.app_server import golden
from e2e.app_server.capture import Capture, capture_scenario
from e2e.app_server.config import CLIENT_BIN, GOLDEN_DIR, REPLAY_BIN, VIBE_DIR
from e2e.app_server.golden import (
    golden_exists,
    golden_path,
    load_golden_requests,
    load_golden_svgs,
    normalize_snapshot,
    store_golden,
)
from e2e.app_server.normalize import normalize_requests
from e2e.app_server.scenario import (
    Action,
    Request,
    Scenario,
    available_scenarios,
    load_scenario,
)
from e2e.app_server.svg import to_svg
from e2e.pty.screen import Snapshot, Terminal
import pytest

_LIVE_DIR = golden_path("_live")


def _assert_scenario_expectations(captured: Capture, scenario: Scenario) -> None:
    if (expected := scenario.expected_actions) is not None:
        assert len(captured.actions) == len(expected) and all(
            want.matches(got)
            for want, got in zip(expected, captured.actions, strict=True)
        ), f"actions {captured.actions!r} do not match {expected!r}"
    if not captured.snapshots:
        return

    snapshot = captured.snapshots[-1]
    screen = "\n".join(snapshot.rows)
    for text in scenario.screen_contains:
        assert text in screen, f"screen is missing {text!r}"
    for text in scenario.screen_excludes:
        assert text not in screen, f"screen contains {text!r}"
    for row, text in scenario.screen_rows.items():
        assert snapshot.rows[row] == text, f"screen row {row} differs"
    if (cursor := scenario.screen_cursor) is not None:
        assert snapshot.cursor == cursor, f"cursor {snapshot.cursor} != {cursor}"
    if (mouse := scenario.mouse_tracking) is not None:
        assert snapshot.mouse_tracking == mouse, f"mouse tracking is not {mouse}"
    if (seen := scenario.mouse_tracking_seen) is not None:
        assert snapshot.mouse_tracking_seen == seen, (
            f"mouse tracking seen is not {seen}"
        )

    clipboard_expected = (
        scenario.expected_clipboard is not None
        or scenario.clipboard_contains
        or scenario.clipboard_excludes
    )
    if not clipboard_expected:
        return

    clipboard = snapshot.clipboard
    assert clipboard is not None, "the client did not emit an OSC 52 payload"
    if scenario.expected_clipboard is not None:
        assert clipboard == scenario.expected_clipboard
    for text in scenario.clipboard_contains:
        assert text in clipboard, f"clipboard is missing {text!r}"
    for text in scenario.clipboard_excludes:
        assert text not in clipboard, f"clipboard contains {text!r}"


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        ([Action("open_url", "expected")], []),
        ([], [Action("open_url", "unexpected")]),
        (
            [Action("open_url", re.compile(r"file:///tmp/a-\w+\.png"))],
            [Action("open_url", "file:///tmp/a-1.png.txt")],
        ),
    ],
)
def test_scenario_expectations_reject_wrong_actions(
    expected: list[Action], actual: list[Action]
) -> None:
    scenario = Scenario(name="test", steps=[], expected_actions=expected)

    with pytest.raises(AssertionError):
        _assert_scenario_expectations(Capture([], actual, []), scenario)


def test_scenario_expectations_accept_a_pattern_action() -> None:
    expected = [Action("open_url", re.compile(r"file:///tmp/a-\w+\.png"))]
    scenario = Scenario(name="test", steps=[], expected_actions=expected)

    _assert_scenario_expectations(
        Capture([], [Action("open_url", "file:///tmp/a-x1.png")], []), scenario
    )


def test_scenario_expectations_reject_wrong_clipboard() -> None:
    scenario = Scenario(name="test", steps=[], expected_clipboard="expected")
    captured = Capture([Snapshot("final", (), "actual")], [], [])

    with pytest.raises(AssertionError):
        _assert_scenario_expectations(captured, scenario)


def test_scenario_expectations_reject_wrong_cursor() -> None:
    scenario = Scenario(name="test", steps=[], screen_cursor=(0, 0))
    captured = Capture([Snapshot("final", (), cursor=(0, 1))], [], [])

    with pytest.raises(AssertionError):
        _assert_scenario_expectations(captured, scenario)


@pytest.mark.parametrize("tracking", [False, True])
def test_scenario_expectations_reject_wrong_mouse_tracking(tracking: bool) -> None:
    scenario = Scenario(name="test", steps=[], mouse_tracking=not tracking)
    captured = Capture([Snapshot("final", (), mouse_tracking=tracking)], [], [])

    with pytest.raises(AssertionError):
        _assert_scenario_expectations(captured, scenario)


@pytest.mark.parametrize("seen", [False, True])
def test_scenario_expectations_reject_wrong_mouse_tracking_seen(seen: bool) -> None:
    scenario = Scenario(name="test", steps=[], mouse_tracking_seen=not seen)
    captured = Capture([Snapshot("final", (), mouse_tracking_seen=seen)], [], [])

    with pytest.raises(AssertionError):
        _assert_scenario_expectations(captured, scenario)


def test_svg_groups_text_without_merging_styles() -> None:
    terminal = Terminal(40, 24)
    terminal.feed(b"plain  text \x1b[1;38;2;255;0;0;48;2;0;0;255mbold red\x1b[0m end")

    svg = ElementTree.fromstring(to_svg(terminal.snapshot("styled"), "test"))
    nodes = [
        node
        for node in svg.findall(".//{*}text")
        if node.get("y") == "20" and node.text and node.text.strip()
    ]

    assert [node.text for node in nodes] == [
        "plain\u00a0\u00a0text\u00a0",
        "bold\u00a0red",
        "\u00a0end",
    ]
    assert [node.attrib["x"].split()[0] for node in nodes] == ["0", "146.4", "244"]
    assert nodes[0].attrib["x"].split() == [
        f"{round(i * 12.2, 2):g}" for i in range(12)
    ]
    assert all("textLength" not in node.attrib for node in nodes)
    assert all(
        node.get("style") == "font-variant-ligatures:none;font-kerning:none"
        for node in nodes
    )
    assert nodes[0].get("class") == nodes[2].get("class")
    assert nodes[0].get("class") != nodes[1].get("class")
    style = svg.find("{*}style")
    assert style is not None and style.text is not None
    assert "fill: #ff0000;font-weight: bold" in style.text
    background = [
        node for node in svg.findall(".//{*}rect") if node.get("fill") == "#0000ff"
    ]
    assert [(node.get("x"), node.get("width")) for node in background] == [
        ("146.4", "97.6")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\u2500\u2500\u2588\u2588", [("\u2500\u2500\u2588\u2588", "0")]),
        ("ab界X", [("ab", "0"), ("界", "24.4"), ("X", "61")]),
        ("abx\u0301Y", [("ab", "0"), ("x\u0301", "24.4"), ("Y", "36.6")]),
        ("❤\ufe0fX", [("❤\ufe0f", "0"), ("X", "24.4")]),
        ("1\ufe0f\u20e3X", [("1\ufe0f\u20e3", "0"), ("X", "24.4")]),
        ("\x1b[4ma b\x1b[0m", [("a", "0"), ("b", "24.4")]),
        ("\x1b[9ma b\x1b[0m", [("a", "0"), ("b", "24.4")]),
    ],
)
def test_svg_preserves_unicode_and_decorated_spaces(
    text: str, expected: list[tuple[str, str]]
) -> None:
    terminal = Terminal(40, 120)
    terminal.feed(text.encode())

    svg = ElementTree.fromstring(to_svg(terminal.snapshot("unicode"), "test"))
    visible = [
        (node.text.rstrip("\u00a0"), node.attrib["x"].split()[0])
        for node in svg.findall(".//{*}text")
        if node.get("y") == "20" and node.text and node.text.strip()
    ]
    assert visible == expected


@pytest.mark.parametrize("elapsed", ["12s", "1m5s", "2h1m0s"])
def test_elapsed_counter_normalizes_to_one_width(elapsed: str) -> None:
    def svg(counter: str) -> str:
        terminal = Terminal(40, 120)
        terminal.feed(f"   Generating… ({counter} Esc to interrupt)".encode())
        return to_svg(normalize_snapshot(terminal.snapshot("loading")), "test")

    assert svg(elapsed) == svg("3s")


def test_golden_keeps_snapshots_in_each_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    terminal = Terminal(40, 120)
    terminal.feed(b"same screen")
    first = terminal.snapshot("first")
    second = replace(first, label="second")

    store_golden("one", [first, second], [])
    store_golden("two", [second], [])

    assert {path.name for path in tmp_path.iterdir()} == {"one", "two"}
    assert sorted(path.name for path in (tmp_path / "one").iterdir()) == [
        "requests.json",
        "snapshot_00_first.svg",
        "snapshot_01_second.svg",
    ]
    assert load_golden_svgs("one") == [
        to_svg(first, "one [first]"),
        to_svg(second, "one [second]"),
    ]
    assert load_golden_svgs("two") == [to_svg(second, "two [second]")]
    assert load_golden_requests("one") == []

    terminal.feed(b" changed")
    changed = terminal.snapshot("changed")
    store_golden("one", [changed], [])
    assert [path.name for path in (tmp_path / "one").glob("*.svg")] == [
        "snapshot_00_changed.svg"
    ]
    assert load_golden_svgs("one") == [to_svg(changed, "one [changed]")]
    assert load_golden_svgs("two") == [to_svg(second, "two [second]")]

    store_golden("two", [], [])
    assert [path.name for path in (tmp_path / "two").iterdir()] == ["requests.json"]
    assert load_golden_svgs("two") == []


@pytest.mark.parametrize("ending", ["", "\n"])
def test_golden_loading_ignores_only_the_file_terminator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str
) -> None:
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    directory = tmp_path / "one"
    directory.mkdir()
    svg = "<svg>\n<text> keep spaces </text>\n</svg>"
    (directory / "snapshot_00_step0.svg").write_text(svg + ending)

    assert load_golden_svgs("one") == [svg]


def test_golden_requests_are_written_with_sorted_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)

    store_golden("one", [], [Request("m", {"b": {"d": 1, "c": 2}, "a": 3})])

    params = json.loads((tmp_path / "one" / "requests.json").read_text())[0]["params"]
    assert list(params) == ["a", "b"]
    assert list(params["b"]) == ["c", "d"]


def test_golden_requests_mask_the_initialize_client_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    initialize = Request("initialize", {"clientInfo": {"version": "2.26.0"}})

    store_golden("one", [], [initialize])

    stored = json.loads((tmp_path / "one" / "requests.json").read_text())
    assert stored[0]["params"]["clientInfo"]["version"] == "<client-version>"


def test_golden_requests_keep_other_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(golden, "GOLDEN_DIR", tmp_path)
    feedback = Request("feedback/record", {"properties": {"version": "2.26.0"}})

    store_golden("one", [], [feedback])

    stored = json.loads((tmp_path / "one" / "requests.json").read_text())
    assert stored[0]["params"]["properties"]["version"] == "2.26.0"


def test_normalize_snapshot_masks_the_update_dialog_current_version() -> None:
    terminal = Terminal(24, 120)
    terminal.feed("2.26.0 → 99.0.0   localhost,127.0.0.1   v9.9.9-fixture".encode())

    normalized = normalize_snapshot(terminal.snapshot("update"))

    assert normalized.rows[0] == (
        "#.##.# → 99.0.0   localhost,127.0.0.1   v9.9.9-fixture"
    )


def test_goldens_are_environment_independent() -> None:
    """Environment-dependent values must be masked or pinned, never stored."""
    cargo = (VIBE_DIR / "vibe" / "cli-rust" / "Cargo.toml").read_text()
    version = re.search(r'^version = "(.+)"', cargo, re.MULTILINE).group(1)
    volatile = {
        "client version": version,
        "checkout path": str(VIBE_DIR),
        "home directory": str(Path.home()),
        # Only the harness's run-generated temp homes (mkdtemp `e2e_*` dirs)
        # leak; scenario-pinned paths under the shared temp dir are deterministic.
        "generated temp home": os.path.join(tempfile.gettempdir(), "e2e_"),
    }
    offenders = sorted(
        f"{path.relative_to(GOLDEN_DIR).as_posix()} ({label})"
        for path in GOLDEN_DIR.rglob("*")
        if path.is_file() and path.relative_to(GOLDEN_DIR).parts[0] != "_live"
        for label, needle in volatile.items()
        if needle in path.read_text()
    )
    assert not offenders, (
        f"goldens contain environment-dependent values: {offenders};"
        " mask them in normalize.py or pin the scenario instead"
    )


@pytest.mark.parametrize("name", available_scenarios())
def test_matches_golden(name: str) -> None:
    """The capture must match the committed golden for SVGs and requests."""
    scenario = load_scenario(name)
    if scenario.skip_reason:
        pytest.skip(scenario.skip_reason)
    expected_titles = scenario.expected_titles
    if expected_titles is not None and scenario.capture_startup:
        expected_titles = expected_titles[1:]
    scenario.clamp_capture_startup()
    if not CLIENT_BIN.exists() or not REPLAY_BIN.exists():
        pytest.skip("Client binary missing (run `make build_test`)")

    captured = capture_scenario(scenario)
    if expected_titles is not None:
        assert (
            tuple(snapshot.title for snapshot in captured.snapshots) == expected_titles
        )
    _assert_scenario_expectations(captured, scenario)
    if not golden_exists(name):
        if not captured.snapshots and not captured.requests:
            pytest.fail(
                f"{name}: captured nothing, refusing to store an empty golden",
                pytrace=False,
            )
        store_golden(
            name, [normalize_snapshot(s) for s in captured.snapshots], captured.requests
        )
        warnings.warn(
            f"{name}: no golden, one was generated — review it before committing",
            stacklevel=2,
        )
        return

    golden_svgs = load_golden_svgs(name)
    golden_requests = load_golden_requests(name)

    if normalize_requests(captured.requests) != golden_requests:
        _save_live_svgs(name, captured.snapshots)
        pytest.fail(f"{name}: app-server request mismatch", pytrace=False)

    if len(captured.snapshots) != len(golden_svgs):
        _save_live_svgs(name, captured.snapshots)
        pytest.fail(
            f"{name}: {len(captured.snapshots)} snapshots vs "
            f"{len(golden_svgs)} goldens",
            pytrace=False,
        )

    for i, (snap, golden_svg) in enumerate(
        zip(captured.snapshots, golden_svgs, strict=True)
    ):
        norm = normalize_snapshot(snap)
        live = to_svg(norm, f"{name} [{snap.label}]")
        if live != golden_svg:
            _save_live_svgs(name, captured.snapshots)
            diff = next(
                difflib.unified_diff(
                    golden_svg.splitlines(keepends=True),
                    live.splitlines(keepends=True),
                    f"golden/{i}",
                    f"live/{i}",
                    n=1,
                ),
                None,
            )
            pytest.fail(
                f"{name}: snapshot {i} ({snap.label}) SVG mismatch"
                + (f"\n{diff}" if diff else ""),
                pytrace=False,
            )


def _save_live_svgs(name: str, snapshots: list) -> None:
    """Write live SVGs for a failing scenario under goldens/_live/<scenario>/.

    The committed goldens stay untouched. The live captures land next to them
    so a single ``diff goldens/<scenario>/snapshot_*.svg goldens/_live/<scenario>/``
    reveals the platform-specific difference.
    """
    out_dir = _LIVE_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, snap in enumerate(snapshots):
        slug = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in snap.label)
        norm = normalize_snapshot(snap)
        svg = to_svg(norm, f"{name} [{snap.label}]")
        (out_dir / f"snapshot_{i:02d}_{slug}.svg").write_text(svg)
