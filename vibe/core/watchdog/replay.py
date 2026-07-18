from __future__ import annotations

import json

from vibe.core.utils.io import read_safe
from vibe.core.watchdog.paths import WatchdogPaths


def render_replay(paths: WatchdogPaths) -> str:
    if not paths.events.exists():
        return f"No Watchdog events found for {paths.run_dir.name}."
    lines: list[str] = []
    for raw_line in read_safe(paths.events).text.splitlines():
        if not raw_line:
            continue
        record = json.loads(raw_line)
        sequence = record.get("sequence", "?")
        kind = record.get("kind", "unknown")
        epoch = record.get("epoch")
        suffix = f" epoch={epoch}" if epoch is not None else ""
        lines.append(f"{sequence:>4} {kind}{suffix}")
    if paths.state.exists():
        state = json.loads(read_safe(paths.state).text)
        incident = state.get("incident") or {}
        lines.append(
            "STATE "
            f"phase={state.get('phase')} observer={state.get('observer_state')} "
            f"incident={incident.get('state', 'none')} epoch={state.get('epoch')}"
        )
    return "\n".join(lines)
