"""Tests for Vibe-specific terminal parity normalization."""

from __future__ import annotations

from e2e.app_server import parity
from e2e.app_server.config import COLUMNS, ROWS, VIBE_DIR
from e2e.app_server.parity import (
    attr_cells,
    hyperlink_cells,
    normalize,
    requests_match,
    snapshots_match,
)
from e2e.app_server.scenario import Request
from e2e.pty.screen import Attr, Snapshot, Terminal
import pytest


def test_attributes_are_part_of_visible_parity() -> None:
    dimmed = Terminal(ROWS, COLUMNS)
    plain = Terminal(ROWS, COLUMNS)
    dimmed.feed(b"before \x1b[2mdim\x1b[22m after")
    plain.feed(b"before dim after")
    dimmed_snapshot = dimmed.snapshot("dimmed")
    plain_snapshot = plain.snapshot("plain")

    assert normalize(dimmed_snapshot) == normalize(plain_snapshot)
    assert Attr.DIM in dimmed_snapshot.cells[0][7].attrs
    assert attr_cells(dimmed_snapshot, plain_snapshot)
    assert not snapshots_match(dimmed_snapshot, plain_snapshot)


def test_footer_pid_length_does_not_change_normalized_text() -> None:
    short_pid = Terminal(ROWS, COLUMNS)
    long_pid = Terminal(ROWS, COLUMNS)
    short_pid.feed(b"\x1b[40;1H~/repo [PID 123]    0/600k tokens (0%)")
    long_pid.feed(b"\x1b[40;1H~/repo [PID 1234]   0/600k tokens (0%)")

    assert normalize(short_pid.snapshot("short")) == normalize(
        long_pid.snapshot("long")
    )


def test_checkout_path_is_stable_in_normalized_snapshots() -> None:
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(f"> @{VIBE_DIR}/client-e2e/fixtures/image.png".encode())

    assert normalize(terminal.snapshot("path"))[0] == (
        "> @<vibe-dir>/client-e2e/fixtures/image.png"
    )


def test_volatile_scrollbar_gutter_does_not_change_parity() -> None:
    first = Terminal(ROWS, COLUMNS)
    second = Terminal(ROWS, COLUMNS)
    first.feed(b"\x1b[1;120H\x1b[40m \x1b[2;120H\x1b[44m\xe2\x96\x87\x1b[3;120H ")
    second.feed(b"\x1b[1;120H\x1b[44m\xe2\x96\x87\x1b[2;120H ")

    assert snapshots_match(first.snapshot("first"), second.snapshot("second"))


def test_path_normalization_precedes_scrollbar_column_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def normalized_rows(checkout: str, thumb_row: int) -> list[str]:
        monkeypatch.setattr(parity, "_VIBE_DIR", checkout)
        terminal = Terminal(ROWS, COLUMNS)
        for row in range(2):
            marker = "▇" if row == thumb_row else " "
            terminal.feed(f"\x1b[{row + 1};1H{checkout}{marker}".encode())
        return normalize(terminal.snapshot("scrollbar"))

    assert normalized_rows("/checkout/one", 0) == normalized_rows(
        "/a/much/longer/checkout/two", 1
    )


def test_pulse_spinner_phase_does_not_change_parity() -> None:
    filled = Terminal(ROWS, COLUMNS)
    hollow = Terminal(ROWS, COLUMNS)
    filled.feed("\u25a0 Writing files".encode())
    hollow.feed("\u25a1 Writing files".encode())

    assert snapshots_match(filled.snapshot("filled"), hollow.snapshot("hollow"))


def test_hyperlink_target_is_part_of_visible_parity() -> None:
    linked = Terminal(ROWS, COLUMNS)
    unlinked = Terminal(ROWS, COLUMNS)
    linked.feed(b"\x1b]8;;https://docs.example\x07docs\x1b]8;;\x07")
    unlinked.feed(b"docs")
    linked_snapshot = linked.snapshot("linked")
    unlinked_snapshot = unlinked.snapshot("unlinked")

    assert normalize(linked_snapshot) == normalize(unlinked_snapshot)
    assert hyperlink_cells(linked_snapshot, unlinked_snapshot)
    assert not snapshots_match(linked_snapshot, unlinked_snapshot)


def _footer_snapshot(pid: str) -> Snapshot:
    suffix = "0/600k tokens (0%)"
    prefix = f"~/repo [PID {pid}]"
    padding = " " * (COLUMNS - len(prefix) - len(suffix))
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(f"\x1b[{ROWS};1H{prefix}{padding}{suffix}".encode())
    return terminal.snapshot("footer")


def test_pid_width_does_not_affect_capture_parity() -> None:
    assert snapshots_match(_footer_snapshot("9999"), _footer_snapshot("10000"))


def _truncated_footer_snapshot(pid: str) -> Snapshot:
    """A cwd long enough that the footer overflows and the tail is cut."""
    row = f"~/{'long-path/' * 9}vibe [PID {pid}] 0/600k tokens (0%)"[:COLUMNS]
    terminal = Terminal(ROWS, COLUMNS)
    terminal.feed(f"\x1b[{ROWS};1H{row}".encode())
    return terminal.snapshot("footer")


def test_pid_width_does_not_affect_a_truncated_footer() -> None:
    short = _truncated_footer_snapshot("9999")
    long = _truncated_footer_snapshot("10000")
    assert normalize(short) != normalize(long)
    assert snapshots_match(short, long)


def test_requests_ignore_client_generated_message_ids() -> None:
    def request(message_id: str) -> list[Request]:
        return [
            Request(
                "turn/start",
                {
                    "clientUserMessageId": message_id,
                    "message": [{"type": "text", "text": "hi"}],
                },
            )
        ]

    assert requests_match({"rust": request("rs-1"), "python": request("py-1")})


def test_requests_preserve_distinct_replacement_idempotency_keys() -> None:
    def replacement(idempotency_key: str, entry_id: str) -> list[Request]:
        return [
            Request(
                "session/turn/queue/replace",
                {"idempotencyKey": idempotency_key, "entries": [{"entryId": entry_id}]},
            )
        ]

    assert requests_match({
        "rust": replacement("rs-edit", "rs-message"),
        "python": replacement("py-edit", "py-message"),
    })
    assert not requests_match({
        "rust": replacement("same", "same"),
        "python": replacement("py-edit", "py-message"),
    })
    assert requests_match({
        "rust": replacement("rs-edit-1", "rs-message")
        + replacement("rs-edit-2", "rs-message"),
        "python": replacement("py-edit-1", "py-message")
        + replacement("py-edit-2", "py-message"),
    })
    assert not requests_match({
        "rust": replacement("rs-edit", "rs-message")
        + replacement("rs-edit", "rs-message"),
        "python": replacement("py-edit-1", "py-message")
        + replacement("py-edit-2", "py-message"),
    })


def test_requests_ignore_client_generated_shell_operation_ids() -> None:
    def request(operation_id: str) -> list[Request]:
        return [
            Request(
                "session/shellCommand",
                {"operationId": operation_id, "command": "printf hello"},
            )
        ]

    assert requests_match({"rust": request("rs-1"), "python": request("py-1")})


def test_requests_ignore_agent_switch_hops_before_the_same_target() -> None:
    def switches(*agents: str) -> list[Request]:
        return [Request("session/agent/update", {"agentName": a}) for a in agents]

    assert requests_match({
        "rust": switches("auto-approve", "plan"),
        "python": switches("auto-approve", "ask", "plan"),
    })
    assert not requests_match({
        "rust": switches("auto-approve", "plan"),
        "python": switches("auto-approve"),
    })


def test_requests_detect_different_config_writes() -> None:
    assert not requests_match({
        "rust": [Request("config/write", {"ops": [{"path": "/theme"}]})],
        "python": [Request("config/write", {"ops": [{"path": "/active_model"}]})],
    })
