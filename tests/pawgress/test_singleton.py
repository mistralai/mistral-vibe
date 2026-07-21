from __future__ import annotations

import os

from vibe.overlay import singleton


def test_second_acquire_fails_while_first_held(tmp_path, monkeypatch):
    monkeypatch.setattr(singleton, "pawgress_dir", lambda: tmp_path)
    fd = singleton.acquire_overlay_lock()
    assert fd is not None
    # A second attempt (new open + flock) must fail while the first is held.
    assert singleton.acquire_overlay_lock() is None
    os.close(fd)
    # After release, acquiring works again.
    fd2 = singleton.acquire_overlay_lock()
    assert fd2 is not None
    os.close(fd2)
