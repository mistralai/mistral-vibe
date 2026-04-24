from __future__ import annotations

from vibe.cli.narrator_manager.telemetry import ReadAloudTrackingState


class TestReadAloudTrackingState:
    def test_default_state(self) -> None:
        state = ReadAloudTrackingState()
        assert state.session_id == ""
        assert state.request_time == 0.0
        assert state.play_start_time == 0.0

    def test_reset_generates_session_id(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        assert state.session_id != ""
        assert len(state.session_id) == 36  # UUID format

    def test_reset_generates_unique_session_ids(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        first_id = state.session_id
        state.reset()
        assert state.session_id != first_id

    def test_reset_clears_play_start_time(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        state.mark_play_started()
        assert state.play_start_time > 0.0
        state.reset()
        assert state.play_start_time == 0.0

    def test_mark_play_started(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        state.mark_play_started()
        assert state.play_start_time > 0.0

    def test_time_to_first_read_s(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        state.mark_play_started()
        ttfr = state.time_to_first_read_s()
        assert ttfr >= 0.0

    def test_time_to_first_read_s_before_play(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        assert state.time_to_first_read_s() == 0.0

    def test_elapsed_since_play_s(self) -> None:
        state = ReadAloudTrackingState()
        state.reset()
        state.mark_play_started()
        elapsed = state.elapsed_since_play_s()
        assert elapsed >= 0.0

    def test_elapsed_since_play_s_before_play(self) -> None:
        state = ReadAloudTrackingState()
        assert state.elapsed_since_play_s() == 0.0
