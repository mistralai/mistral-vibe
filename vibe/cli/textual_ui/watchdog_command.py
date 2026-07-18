from __future__ import annotations

from vibe.core.watchdog.runtime import WatchdogRuntime

WATCHDOG_USAGE = "Usage: /watchdog [status|on|off|pause|resume|snapshot|recover|replay]"


def format_watchdog_status(runtime: WatchdogRuntime | None) -> str:
    if runtime is None:
        return """## Watchdog

```text
WATCHDOG [OFF]
|
`-- monitoring disabled
```

Run `/watchdog on` to start monitoring and recovery.
"""

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

```text
WATCHDOG [{status.upper()}]
|
+-- observer : {state.observer_state.value}
+-- phase    : {state.phase.value}
+-- incident : {incident_state}
+-- detector : {detector}
+-- epoch    : {state.epoch}
`-- recovery : {recovery}

observe --> detect --> recover --> verify
```

Run: `{runtime.run_id}`

Artifacts: `{runtime.paths.run_dir}`

Controls: `/watchdog pause|resume|snapshot|recover|replay|off`
"""
