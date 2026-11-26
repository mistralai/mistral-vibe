from __future__ import annotations

import signal
import sys
import threading
import time


def _blocks_in_select(thread_id: int) -> bool:
    frame = sys._current_frames().get(thread_id)
    return (
        frame is not None
        and frame.f_code.co_name == "select"
        and frame.f_code.co_filename.endswith("selectors.py")
        and frame.f_locals.get("timeout") != 0
    )


def _sigterm_itself_once_waiting(loop_thread_id: int) -> None:
    while not _blocks_in_select(loop_thread_id):
        time.sleep(0.001)
    signal.pthread_kill(threading.get_ident(), signal.SIGTERM)


def sigterm_another_thread_once_the_loop_waits() -> threading.Thread:
    sender = threading.Thread(
        target=_sigterm_itself_once_waiting, args=(threading.get_ident(),)
    )
    sender.start()
    return sender
