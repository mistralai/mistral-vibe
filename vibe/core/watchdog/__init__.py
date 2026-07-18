from __future__ import annotations

from vibe.core.watchdog._port import WatchdogObserverPort
from vibe.core.watchdog.continuation import ContinuationCoordinator
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.fingerprint import (
    canonical_json,
    fingerprint,
    fingerprint_call,
    fingerprint_error,
    fingerprint_evidence,
    fingerprint_repository,
    fingerprint_result,
    sanitize_artifact,
)
from vibe.core.watchdog.incident import IncidentEngine, IncidentTransition
from vibe.core.watchdog.models import (
    Evidence,
    Incident,
    IncidentState,
    ObserverState,
    RecoveryDecision,
    RecoveryStrategy,
    RunPhase,
    RunState,
)
from vibe.core.watchdog.paths import WatchdogPaths
from vibe.core.watchdog.queue import QueuePutResult, WatchdogEventQueue
from vibe.core.watchdog.recovery import RecoveryCoordinator
from vibe.core.watchdog.recovery_port import (
    CancelResult,
    IdleResult,
    QuiesceResult,
    RecoveryPort,
)
from vibe.core.watchdog.reducer import (
    EventIdentityError,
    EventOrderError,
    WatchdogReducerError,
    apply_event,
)
from vibe.core.watchdog.store import (
    WatchdogSchemaVersionError,
    WatchdogStorageError,
    WatchdogStore,
)
from vibe.core.watchdog.supervisor import ObserveOnlySupervisor, observe_stream

__all__ = [
    "CancelResult",
    "ContinuationCoordinator",
    "EventIdentityError",
    "EventKind",
    "EventOrderError",
    "Evidence",
    "IdleResult",
    "Incident",
    "IncidentEngine",
    "IncidentState",
    "IncidentTransition",
    "ObserveOnlySupervisor",
    "ObserverState",
    "QueuePutResult",
    "QuiesceResult",
    "RecoveryCoordinator",
    "RecoveryDecision",
    "RecoveryPort",
    "RecoveryStrategy",
    "RunPhase",
    "RunState",
    "WatchdogEvent",
    "WatchdogEventQueue",
    "WatchdogObserverPort",
    "WatchdogPaths",
    "WatchdogReducerError",
    "WatchdogSchemaVersionError",
    "WatchdogStorageError",
    "WatchdogStore",
    "apply_event",
    "canonical_json",
    "fingerprint",
    "fingerprint_call",
    "fingerprint_error",
    "fingerprint_evidence",
    "fingerprint_repository",
    "fingerprint_result",
    "observe_stream",
    "sanitize_artifact",
]
