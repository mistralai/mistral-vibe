from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
import threading
import time

import pytest

from vibe.core.log_reader import LogEntry, LogReader


def _wait_for(condition: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


class TestLogEntry:
    def test_log_entry_is_frozen(self) -> None:
        entry = LogEntry(
            timestamp=datetime.now(),
            ppid=1,
            pid=123,
            level="INFO",
            message="test",
            raw_line="raw",
            line_number=1,
        )
        with pytest.raises(FrozenInstanceError):
            entry.message = "modified"  # type: ignore[misc]


class TestLogReaderParsing:
    @pytest.fixture
    def log_file(self, tmp_path: Path) -> Path:
        return tmp_path / "test.log"

    def test_parses_valid_info_log(self, log_file: Path) -> None:
        log_file.write_text(
            "2026-02-08T10:30:45.123000+00:00 1234 5678 INFO Test message\n"
        )
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert len(result.entries) == 1
        assert result.entries[0].level == "INFO"
        assert result.entries[0].message == "Test message"

    def test_parses_valid_error_log(self, log_file: Path) -> None:
        log_file.write_text(
            "2026-02-08T10:30:45.123000+00:00 1234 5678 ERROR Error message\n"
        )
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries[0].level == "ERROR"

    def test_parses_log_with_ppid_pid(self, log_file: Path) -> None:
        log_file.write_text("2026-02-08T10:30:45.123000+00:00 1111 2222 DEBUG msg\n")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries[0].ppid == 1111
        assert result.entries[0].pid == 2222

    def test_skips_invalid_log_lines(self, log_file: Path) -> None:
        log_file.write_text(
            "invalid line\n2026-02-08T10:30:45.123000+00:00 1 2 INFO valid\n"
        )
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert len(result.entries) == 1
        assert result.entries[0].message == "valid"

    def test_skips_multiline_continuations(self, log_file: Path) -> None:
        log_file.write_text(
            "2026-02-08T10:30:45.123000+00:00 1 2 INFO msg\n  continuation\n"
        )
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert len(result.entries) == 1

    def test_handles_empty_file(self, log_file: Path) -> None:
        log_file.write_text("")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries == []

    def test_handles_missing_file(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.log"
        reader = LogReader(log_file=missing)
        result = reader.get_logs()
        assert result.entries == []

    def test_skips_line_with_invalid_timestamp(self, log_file: Path) -> None:
        log_file.write_text("not-a-timestamp 1 2 INFO message\n")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries == []

    def test_skips_line_with_invalid_level(self, log_file: Path) -> None:
        log_file.write_text("2026-02-08T10:30:45.123000+00:00 1 2 UNKNOWN message\n")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries == []

    def test_skips_line_missing_pid(self, log_file: Path) -> None:
        log_file.write_text("2026-02-08T10:30:45.123000+00:00 1 INFO message\n")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert result.entries == []

    def test_skips_empty_lines(self, log_file: Path) -> None:
        log_file.write_text("\n\n2026-02-08T10:30:45.123000+00:00 1 2 INFO valid\n\n")
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert len(result.entries) == 1

    def test_handles_extremely_long_lines(self, log_file: Path) -> None:
        long_message = "x" * (128 * 1024)  # 128KB message
        log_file.write_text(
            f"2026-02-08T10:30:45.123000+00:00 1 2 INFO {long_message}\n"
            f"2026-02-08T10:30:46.123000+00:00 1 2 INFO short\n"
        )
        reader = LogReader(log_file=log_file)
        result = reader.get_logs()
        assert len(result.entries) == 2
        assert result.entries[0].message == "short"
        assert result.entries[1].message == long_message


class TestLogReaderMassiveLogs:
    @pytest.fixture
    def log_file(self, tmp_path: Path) -> Path:
        return tmp_path / "test.log"

    def test_limit_prevents_reading_entire_large_file(self, log_file: Path) -> None:
        num_lines = 10_000
        lines = [
            f"2026-02-08T10:30:{i % 60:02d}.{i:06d}+00:00 1 2 INFO Message {i}\n"
            for i in range(num_lines)
        ]
        log_file.write_text("".join(lines))

        reader = LogReader(log_file=log_file)
        result = reader.get_logs(limit=10)

        assert len(result.entries) == 10
        assert result.has_more is True

    def test_handles_file_with_mostly_invalid_lines(self, log_file: Path) -> None:
        invalid_lines = ["garbage line\n"] * 1000
        valid_line = "2026-02-08T10:30:45.123000+00:00 1 2 INFO valid entry\n"
        log_file.write_text("".join(invalid_lines) + valid_line)

        reader = LogReader(log_file=log_file)
        result = reader.get_logs(limit=100)

        assert len(result.entries) == 1
        assert result.entries[0].message == "valid entry"

    def test_handles_binary_garbage_in_file(self, log_file: Path) -> None:
        binary_garbage = bytes(range(256))
        valid_line = b"2026-02-08T10:30:45.123000+00:00 1 2 INFO valid after binary\n"
        log_file.write_bytes(binary_garbage + b"\n" + valid_line)

        reader = LogReader(log_file=log_file)
        result = reader.get_logs()

        assert len(result.entries) == 1
        assert result.entries[0].message == "valid after binary"

    def test_handles_null_bytes_in_lines(self, log_file: Path) -> None:
        line_with_nulls = (
            "2026-02-08T10:30:45.123000+00:00 1 2 INFO msg\x00with\x00nulls\n"
        )
        valid_line = "2026-02-08T10:30:46.123000+00:00 1 2 INFO clean message\n"
        log_file.write_text(line_with_nulls + valid_line)

        reader = LogReader(log_file=log_file)
        result = reader.get_logs()

        assert len(result.entries) == 2
        assert result.entries[0].message == "clean message"
        assert result.entries[1].message == "msg\x00with\x00nulls"

    def test_handles_massive_single_line_without_newline(self, log_file: Path) -> None:
        massive_line = "x" * (1024 * 1024)
        log_file.write_text(massive_line)

        reader = LogReader(log_file=log_file)
        result = reader.get_logs()

        assert result.entries == []


class TestLogReaderPagination:
    @pytest.fixture
    def log_file_with_entries(self, tmp_path: Path) -> Path:
        log_file = tmp_path / "test.log"
        lines = [
            f"2026-02-08T10:30:{i:02d}.000000+00:00 1 2 INFO Message {i}\n"
            for i in range(10)
        ]
        log_file.write_text("".join(lines))
        return log_file

    def test_returns_logs_newest_first(self, log_file_with_entries: Path) -> None:
        reader = LogReader(log_file=log_file_with_entries)
        result = reader.get_logs()
        assert "Message 9" in result.entries[0].message

    def test_limit_restricts_results(self, log_file_with_entries: Path) -> None:
        reader = LogReader(log_file=log_file_with_entries)
        result = reader.get_logs(limit=3)
        assert len(result.entries) == 3

    def test_has_more_false_when_exhausted(self, log_file_with_entries: Path) -> None:
        reader = LogReader(log_file=log_file_with_entries)
        result = reader.get_logs(limit=100)
        assert result.has_more is False

    def test_cursor_continues_from_previous_position(
        self, log_file_with_entries: Path
    ) -> None:
        reader = LogReader(log_file=log_file_with_entries)
        page1 = reader.get_logs(limit=3)
        assert len(page1.entries) == 3
        assert page1.has_more is True
        assert page1.cursor is not None

        page2 = reader.get_logs(limit=3, offset=page1.cursor)
        assert len(page2.entries) == 3
        assert page2.has_more is True
        assert page2.entries[0].message != page1.entries[-1].message


class TestLogReaderWatcher:
    @pytest.fixture
    def log_reader(self, tmp_path: Path) -> Generator[LogReader, None, None]:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        reader = LogReader(log_file=log_file)
        yield reader
        reader.shutdown()

    def test_start_watching_sets_is_watching(self, log_reader: LogReader) -> None:
        log_reader.start_watching()
        assert log_reader.is_watching is True

    def test_stop_watching_clears_is_watching(self, log_reader: LogReader) -> None:
        log_reader.start_watching()
        log_reader.stop_watching()
        assert log_reader.is_watching is False

    def test_consumer_receives_new_entries(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        received: list[LogEntry] = []
        reader = LogReader(log_file=log_file, consumer=received.append)
        try:
            reader.start_watching()

            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:45.123000+00:00 1 2 INFO New entry\n")

            assert _wait_for(lambda: len(received) >= 1)
            assert received[0].message == "New entry"
        finally:
            reader.shutdown()

    def test_toggle_watching_on_off(self, log_reader: LogReader) -> None:
        log_reader.start_watching()
        assert log_reader.is_watching is True
        log_reader.stop_watching()
        assert log_reader.is_watching is False
        log_reader.start_watching()
        assert log_reader.is_watching is True

    def test_set_consumer_updates_callback(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        received: list[LogEntry] = []
        reader = LogReader(log_file=log_file)
        try:
            reader.set_consumer(received.append)
            reader.start_watching()

            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:45.123000+00:00 1 2 INFO Entry\n")

            assert _wait_for(lambda: len(received) >= 1)
        finally:
            reader.shutdown()

    def test_consumer_can_call_get_logs_without_deadlock(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        reader = LogReader(log_file=log_file, poll_interval=0.05)
        callback_completed = threading.Event()
        callback_errors: list[Exception] = []

        def consumer(_: LogEntry) -> None:
            try:
                reader.get_logs(limit=1)
            except Exception as exc:
                callback_errors.append(exc)
            finally:
                callback_completed.set()

        reader.set_consumer(consumer)
        try:
            reader.start_watching()
            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:45.123000+00:00 1 2 INFO Entry\n")

            assert callback_completed.wait(timeout=1.0)
            assert callback_errors == []
        finally:
            reader.shutdown()


class TestLogReaderCleanup:
    def test_shutdown_stops_watching(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        reader = LogReader(log_file=log_file)
        reader.start_watching()
        assert reader.is_watching is True
        reader.shutdown()
        assert reader.is_watching is False


class TestLogReaderLineNumbers:
    def test_line_numbers_relative_from_end(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        lines = [
            f"2026-02-08T10:30:{i:02d}.000000+00:00 1 2 INFO Message {i}\n"
            for i in range(5)
        ]
        log_file.write_text("".join(lines))

        reader = LogReader(log_file=log_file)
        result = reader.get_logs(limit=5)

        assert result.entries[0].line_number == 1  # Newest
        assert result.entries[0].message == "Message 4"
        assert result.entries[1].line_number == 2  # Second newest
        assert result.entries[1].message == "Message 3"

    def test_live_entries_have_zero_line_number(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        log_file.write_text("")
        received: list[LogEntry] = []
        reader = LogReader(log_file=log_file, consumer=received.append)
        try:
            reader.start_watching()

            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:45.123000+00:00 1 2 INFO Live entry\n")

            assert _wait_for(lambda: len(received) >= 1)
            assert received[0].line_number == 0
            assert received[0].message == "Live entry"
        finally:
            reader.shutdown()


class TestLogReaderCursorDrift:
    def test_cursor_stable_with_new_logs(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        lines = [
            f"2026-02-08T10:30:{i:02d}.000000+00:00 1 2 INFO Message {i}\n"
            for i in range(10)
        ]
        log_file.write_text("".join(lines))

        received: list[LogEntry] = []
        reader = LogReader(log_file=log_file, consumer=received.append)

        try:
            page1 = reader.get_logs(limit=3)
            assert page1.entries[0].message == "Message 9"
            assert page1.cursor == 3

            reader.start_watching()
            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:50.000000+00:00 1 2 INFO New message 1\n")
                f.write("2026-02-08T10:30:51.000000+00:00 1 2 INFO New message 2\n")

            assert _wait_for(lambda: len(received) >= 2)

            page2 = reader.get_logs(limit=3, offset=page1.cursor)
            assert page2.entries[0].message == "Message 6"
            assert page2.entries[1].message == "Message 5"
            assert page2.entries[2].message == "Message 4"

        finally:
            reader.shutdown()

    def test_stop_watching_resets_counter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        lines = [
            f"2026-02-08T10:30:{i:02d}.000000+00:00 1 2 INFO Message {i}\n"
            for i in range(5)
        ]
        log_file.write_text("".join(lines))

        received: list[LogEntry] = []
        reader = LogReader(log_file=log_file, consumer=received.append)

        try:
            reader.start_watching()

            with log_file.open("a") as f:
                f.write("2026-02-08T10:30:50.000000+00:00 1 2 INFO New message\n")

            assert _wait_for(lambda: len(received) >= 1)

            reader.stop_watching()

            result = reader.get_logs(limit=10)
            assert len(result.entries) == 6
            assert result.entries[0].message == "New message"

        finally:
            reader.shutdown()
