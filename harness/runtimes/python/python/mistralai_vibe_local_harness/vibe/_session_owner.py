"""The client that last opened a root Session, which alone may resume a turn
a stopped process left running in it.

The owner is an opaque JSON object the Host compares for equality. It is kept
beside the store, not in it, so it can be written while another process holds
the Session's lease and no reader of the store sees it.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile

from pydantic import JsonValue

from mistralai_vibe_local_harness.vibe._storage import durable_replace

logger = logging.getLogger(__name__)

_FILE_NAME = "owner.json"


def read_owner(session_root: Path) -> JsonValue:
    """The stamped owner, or None when the Session was never stamped."""
    try:
        return json.loads((session_root / _FILE_NAME).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("Unreadable session owner", exc_info=True)
        return None


def write_owner(session_root: Path, owner: JsonValue) -> None:
    """Stamp ``owner``, or log why not: a stamp must never stop an open. A None
    owner removes the stamp, so no client is offered the Session's turns.
    """
    try:
        if read_owner(session_root) == owner:
            return
        if owner is None:
            (session_root / _FILE_NAME).unlink(missing_ok=True)
            return
        descriptor, name = tempfile.mkstemp(
            prefix=f".{_FILE_NAME}.", suffix=".tmp", dir=session_root
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(json.dumps(owner, sort_keys=True).encode())
                file.flush()
                os.fsync(file.fileno())
            durable_replace(temporary, session_root / _FILE_NAME)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    except OSError:
        logger.warning("Could not stamp a session's owner", exc_info=True)
