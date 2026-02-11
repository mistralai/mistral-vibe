from __future__ import annotations

import random

from vibe.cli.textual_ui.widgets.spinner import SpinnerType, create_spinner


def test_generate_100_frames_no_crash() -> None:
    """Generate 100 frames per spinner type with seeded random for determinism."""
    random.seed(42)
    for spinner_type in SpinnerType:
        spinner = create_spinner(spinner_type)
        for _ in range(100):
            frame = spinner.next_frame()
            assert isinstance(frame, str)
            assert len(frame) > 0
