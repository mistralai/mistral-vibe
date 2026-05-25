from __future__ import annotations

import hashlib

from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.models import (
    EvalResponse,
    ExperimentAttributes,
    FeatureDefinition,
    TrackData,
)
from vibe.core.logger import logger


def hash_api_key(api_key: str) -> str:
    """Stable, anonymous bucketing key derived from the Mistral API key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


class ExperimentManager:
    def __init__(
        self,
        client: RemoteEvalClient | None = None,
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._client = client if client is not None else RemoteEvalClient()
        self._overrides = dict(overrides) if overrides else {}
        self._response: EvalResponse | None = None

    async def initialize(self, attributes: ExperimentAttributes) -> None:
        response = await self._client.evaluate(attributes)
        if response is None:
            return
        self._response = self._filter_to_known_experiments(response)
        self._log_resolved_variants("resolved")

    def hydrate(self, response: EvalResponse) -> None:
        self._response = self._filter_to_known_experiments(response)
        self._log_resolved_variants("restored from session")

    def export_state(self) -> EvalResponse | None:
        return self._response

    @staticmethod
    def _filter_to_known_experiments(response: EvalResponse) -> EvalResponse:
        known = {name.value for name in ExperimentName}
        return EvalResponse(
            features={k: v for k, v in response.features.items() if k in known}
        )

    def _log_resolved_variants(self, source: str) -> None:
        resolved = {name.value: self.get_variant(name) for name in ExperimentName}
        logger.info(
            "Experiment variants %s (resolved=%s, in_experiment=%s)",
            source,
            resolved,
            self.assignments(),
        )

    def get_variant_or_none(self, name: ExperimentName) -> str | None:
        if (override := self._overrides.get(name.value)) is not None:
            return override
        if self._response is not None:
            feature = self._response.features.get(name.value)
            if feature is not None:
                value = feature.resolved_value()
                if isinstance(value, str):
                    return value
        return None

    def get_variant(self, name: ExperimentName) -> str:
        variant = self.get_variant_or_none(name)
        return variant if variant is not None else DEFAULT_VARIANTS[name]

    def assignments(self) -> dict[str, str]:
        result: dict[str, str] = {}
        if self._response is None:
            return dict(self._overrides)
        for feature_key, feature in self._response.features.items():
            for rule in feature.rules:
                for track in rule.tracks:
                    if not track.result.inExperiment:
                        continue
                    label = self._variant_label(feature, track)
                    if label:
                        result[feature_key] = label
            override = self._overrides.get(feature_key)
            if override is not None:
                result[feature_key] = override
        for key, value in self._overrides.items():
            result.setdefault(key, value)
        return result

    @staticmethod
    def _variant_label(feature: FeatureDefinition, track: TrackData) -> str:
        value = track.result.value
        if isinstance(value, str):
            return value
        resolved = feature.resolved_value()
        if isinstance(resolved, str):
            return resolved
        if track.result.key is not None:
            return track.result.key
        if track.result.variationId is not None:
            return str(track.result.variationId)
        return ""

    async def aclose(self) -> None:
        await self._client.aclose()
