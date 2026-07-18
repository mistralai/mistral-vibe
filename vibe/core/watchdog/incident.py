from __future__ import annotations

import hashlib

from vibe.core.watchdog.detectors.base import (
    Detector,
    DetectorObservation,
    DetectorVerdict,
)
from vibe.core.watchdog.events import EventKind, WatchdogEvent
from vibe.core.watchdog.models import Incident, IncidentState, RunState


class IncidentTransition:
    def __init__(self, *, kind: EventKind, incident: Incident) -> None:
        self.kind = kind
        self.incident = incident


class IncidentEngine:
    def __init__(self, detectors: tuple[Detector, ...]) -> None:
        self._detectors = detectors

    def observe(
        self, event: WatchdogEvent, state: RunState
    ) -> list[IncidentTransition]:
        if expired := self._expire(event, state):
            return [expired]
        observations = [
            observation
            for detector in self._detectors
            for observation in detector.observe(event, state)
        ]
        for observation in observations:
            if transition := self._transition(event, state, observation):
                return [transition]
        return []

    def _expire(
        self, event: WatchdogEvent, state: RunState
    ) -> IncidentTransition | None:
        incident = state.incident
        if incident is None or incident.state != IncidentState.SUSPECTED:
            return None
        expiry = max(
            (
                evidence.expires_at_sequence or event.sequence
                for evidence in incident.evidence
            ),
            default=event.sequence,
        )
        if event.sequence <= expiry:
            return None
        closed = incident.model_copy(update={"state": IncidentState.CLOSED})
        return IncidentTransition(kind=EventKind.INCIDENT_CLOSED, incident=closed)

    def _transition(
        self, event: WatchdogEvent, state: RunState, observation: DetectorObservation
    ) -> IncidentTransition | None:
        incident = state.incident
        if observation.verdict == DetectorVerdict.SUSPECTED:
            if incident is not None and incident.state != IncidentState.CLOSED:
                return None
            created = Incident(
                incident_id=_incident_id(
                    event.run_id, observation.detector_id, event.sequence
                ),
                epoch=0,
                state=IncidentState.SUSPECTED,
                owner=observation.detector_id,
                evidence=(observation.evidence,),
            )
            return IncidentTransition(
                kind=EventKind.INCIDENT_SUSPECTED, incident=created
            )
        if observation.verdict == DetectorVerdict.CONFIRMED:
            if incident is None or incident.state == IncidentState.CLOSED:
                incident = Incident(
                    incident_id=_incident_id(
                        event.run_id, observation.detector_id, event.sequence
                    ),
                    epoch=0,
                    state=IncidentState.CONFIRMED,
                    owner=observation.detector_id,
                    evidence=(observation.evidence,),
                )
            else:
                incident = incident.model_copy(
                    update={
                        "state": IncidentState.CONFIRMED,
                        "evidence": (*incident.evidence, observation.evidence),
                    }
                )
            return IncidentTransition(
                kind=EventKind.INCIDENT_CONFIRMED, incident=incident
            )
        if (
            observation.verdict == DetectorVerdict.HEALTHY
            and incident is not None
            and incident.state == IncidentState.SUSPECTED
        ):
            closed = incident.model_copy(update={"state": IncidentState.CLOSED})
            return IncidentTransition(kind=EventKind.INCIDENT_CLOSED, incident=closed)
        return None


def _incident_id(run_id: str, detector_id: str, sequence: int) -> str:
    raw = f"{run_id}:{detector_id}:{sequence}".encode()
    return hashlib.sha256(raw).hexdigest()[:20]
