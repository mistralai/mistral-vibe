from __future__ import annotations

from vibe.core.watchdog.runtime import WatchdogRuntime

WATCHDOG_USAGE = "Usage: /watchdog [status|on|off|pause|resume|snapshot|recover|replay]"


def format_watchdog_status(runtime: WatchdogRuntime | None) -> str:
    if runtime is None:
        return "## Watchdog\n\n- **Status**: disabled\n\n`/watchdog on` to enable."

    state = runtime.supervisor.state
    incident = state.incident
    status = "enabled" if runtime.supervisor.interventions_enabled else "paused"
    incident_state = incident.state.value if incident is not None else "none"
    detector = incident.owner if incident is not None else "none"
    recovery = (
        incident.decision.strategy.value
        if incident is not None and incident.decision is not None
        else "none"
    )
    return f"""## Watchdog

- **Status**: {status}
- **Run**: `{runtime.run_id}`
- **Phase**: {state.phase.value}
- **Observer**: {state.observer_state.value}
- **Incident**: {incident_state}
- **Detector**: {detector}
- **Epoch**: {state.epoch}
- **Last recovery**: {recovery}
- **Artifacts**: `{runtime.paths.run_dir}`
"""
