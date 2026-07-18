from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.watchdog.models import RunPhase


class RecoveryHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    objective: str
    incident_id: str
    epoch: int = Field(ge=0)
    detector: str
    evidence_fingerprint: str
    phase: RunPhase
    repository_fingerprint: str | None = None
    changed_paths: tuple[str, ...] = ()
    failure_fingerprints: tuple[str, ...] = ()
    attempted_strategies: tuple[str, ...] = ()
    prohibited_action: str
    required_next_step: str
    checkpoint_reference: str | None = None
    remaining_attempts: int = Field(ge=0)

    def render(self, *, max_chars: int = 4_000) -> str:
        structured = [
            "[WATCHCAT RECOVERY HANDOFF]",
            f"objective={self.objective}",
            f"incident={self.incident_id}",
            f"epoch={self.epoch}",
            f"detector={self.detector}",
            f"evidence={self.evidence_fingerprint}",
            f"phase={self.phase}",
            f"repository={self.repository_fingerprint or 'unknown'}",
            f"prohibited={self.prohibited_action}",
            f"required={self.required_next_step}",
            f"remaining_attempts={self.remaining_attempts}",
        ]
        optional = [
            f"changed_paths={','.join(self.changed_paths)}",
            f"failures={','.join(self.failure_fingerprints)}",
            f"attempted={','.join(self.attempted_strategies)}",
            f"checkpoint={self.checkpoint_reference or 'none'}",
        ]
        required = "\n".join(structured)
        room = max(max_chars - len(required) - 1, 0)
        return f"{required}\n{' '.join(optional)[:room]}"
