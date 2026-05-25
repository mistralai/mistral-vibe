from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.telemetry.types import AgentEntrypoint


class ExperimentAttributes(BaseModel):
    """Client-side attributes sent to the GrowthBook proxy for evaluation.

    `userId` is the GrowthBook hash attribute used for variant bucketing.
    We use a hash of the Mistral API key to be stable per user without
    leaking the key. The attribute name must match the one selected in the
    experiment's "Assign variation based on attribute" setting on
    GrowthBook (which in turn must be registered as a User Attribute in
    the org's Settings → Attributes).
    """

    userId: str
    entrypoint: AgentEntrypoint
    agent_version: str
    client_name: str | None = None
    client_version: str | None = None
    os: Literal["darwin", "linux", "windows"] | str
    terminal_emulator: str | None = None
    custom_system_prompt: bool = False


class TrackedExperiment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str


class TrackedExperimentResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str | None = None
    variationId: int | None = None
    value: Any = None
    inExperiment: bool | None = None
    hashAttribute: str | None = None
    hashValue: str | None = None
    featureId: str | None = None


class TrackData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    experiment: TrackedExperiment
    result: TrackedExperimentResult


class FeatureRule(BaseModel):
    model_config = ConfigDict(extra="ignore")

    force: Any = None
    tracks: list[TrackData] = Field(default_factory=list)


class FeatureDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    defaultValue: Any = None
    rules: list[FeatureRule] = Field(default_factory=list)

    def resolved_value(self) -> Any:
        for rule in self.rules:
            if rule.force is not None:
                return rule.force
        return self.defaultValue


class EvalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    features: dict[str, FeatureDefinition] = Field(default_factory=dict)
