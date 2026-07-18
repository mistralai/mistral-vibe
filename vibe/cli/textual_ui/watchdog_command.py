from __future__ import annotations

from vibe.core.watchdog.models import ObserverState
from vibe.core.watchdog.runtime import WatchdogRuntime
from vibe.core.watchdog.snapshots import ConversationSnapshot

WATCHDOG_USAGE = "Usage: /watchdog [status|on|off|pause|resume|snapshot|recover]"


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
    signal_quality = (
        "healthy" if state.observer_state == ObserverState.TRUSTED else "degraded"
    )
    return f"""## Watchdog

```text
WATCHDOG [{status.upper()}]
|
+-- signal quality : {signal_quality}
+-- phase          : {state.phase.value}
+-- incident       : {incident_state}
+-- detector       : {detector}
+-- epoch          : {state.epoch}
`-- recovery       : {recovery}

observe --> detect --> recover --> verify
```

Run: `{runtime.run_id}`

Artifacts: `{runtime.paths.run_dir}`

Controls: `/watchdog pause|resume|snapshot|recover|off`
"""


def format_snapshot_list(snapshots: list[ConversationSnapshot]) -> str:
    lines = ["```text", "SNAPSHOTS", "|"]
    for index, snapshot in enumerate(snapshots):
        branch = "`--" if index == len(snapshots) - 1 else "+--"
        lines.append(
            f"{branch} {index}  {snapshot.display_name[:40]}  "
            f"msg:{len(snapshot.messages)}"
        )
    lines.extend(["```", "", "Apply by index or hash: `/watchdog snapshot apply 0`"])
    return "\n".join(lines)
