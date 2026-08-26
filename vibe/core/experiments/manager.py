from __future__ import annotations

import hashlib
import json

from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.models import (
    EvalResponse,
    ExperimentAttributes,
    FeatureDefinition,
    TrackData,
)
from vibe.core.telemetry.types import ExperimentAssignment
from vibe.observability.logging import logger


def hash_api_key(api_key: str) -> str:
    """Stable, anonymous bucketing key derived from the Mistral API key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


def config_variants_from_response(response: EvalResponse) -> dict[str, str]:
    """Config-layer variants for a cached eval response, computed without network."""
    manager = ExperimentManager(client=RemoteEvalClient())
    manager.hydrate(response, source="cache")
    return manager.config_variants()


class ExperimentManager:
    def __init__(self, client: RemoteEvalClient | None = None) -> None:
        self._client = client if client is not None else RemoteEvalClient()
        self._response: EvalResponse | None = None
        self._attributes: ExperimentAttributes | None = None

    async def initialize(self, attributes: ExperimentAttributes) -> None:
        # Retain the exact attribute snapshot sent to GrowthBook for bucketing so
        # telemetry can emit it on the exposure event. Keeping the two in sync is
        # what lets the datalake exposures be segmented by the same dimensions
        # GrowthBook assigns on (see ``attributes``).
        self._attributes = attributes
        response = await self._client.evaluate(attributes)
        if response is None:
            return
        self._response = self._filter_to_known_experiments(response)
        self._log_resolved_variants("resolved")

    def attributes(self) -> ExperimentAttributes | None:
        """Return the attribute snapshot used for the last GrowthBook eval.

        This is the exact payload sent to the proxy for variant bucketing. It is
        emitted on the exposure telemetry event so warehouse-side analysis can
        dimension on the same attributes GrowthBook assigned on. ``None`` until
        :meth:`initialize` has run (e.g. resumed sessions hydrated from state).
        """
        return self._attributes

    def set_attributes(self, attributes: ExperimentAttributes) -> None:
        """Set the attribute snapshot without a remote eval.

        Used when there is nothing to evaluate (no Mistral provider, so no
        A/B bucketing) but telemetry still needs ``experiment_attributes`` to
        carry the sentinel plan fields (planType/planName = NO_PLAN_DATA).
        """
        self._attributes = attributes

    def hydrate(
        self,
        response: EvalResponse,
        *,
        attributes: ExperimentAttributes | None = None,
        source: str = "session",
    ) -> None:
        # Restore the bucketing snapshot too, not just the eval response. The
        # snapshot is what telemetry emits as ``experiment_attributes``; without
        # it every event on a resumed session would miss planName/planType.
        self._attributes = attributes
        self._response = self._filter_to_known_experiments(response)
        self._log_resolved_variants(f"restored from {source}")

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
        """Return GrowthBook's resolved value as a string, including defaults.

        Object/array valued experiments are serialized to their JSON string form
        so callers get the real payload instead of falling back to the
        client-side default.
        """
        if self._response is not None:
            feature = self._response.features.get(name.value)
            if feature is not None:
                value = feature.resolved_value()
                if isinstance(value, str):
                    return value
                if value is not None:
                    return json.dumps(value)
        return None

    def get_variant(self, name: ExperimentName) -> str:
        """Return the resolved remote value, falling back to Vibe's default."""
        variant = self.get_variant_or_none(name)
        return variant if variant is not None else DEFAULT_VARIANTS[name]

    def config_variants(self) -> dict[str, str]:
        """Return experiment values allowed to override config layers."""
        result: dict[str, str] = {
            a.experiment_id: a.variation_name for a in self.assignments()
        }
        if self._response is None:
            return result

        for name in ExperimentName:
            if name.value in result:
                continue
            feature = self._response.features.get(name.value)
            if feature is None:
                continue
            if (variant := self._forced_variant_or_none(feature)) is not None:
                result[name.value] = variant
        return result

    def assignments(self) -> list[ExperimentAssignment]:
        """Return confirmed experiment exposures for telemetry only.

        At most one assignment per experiment_id (last confirmed track wins).
        The dbt exposures model treats one row per (session_id, experiment_id),
        so a duplicated experiment_id would read as a GrowthBook
        multiple-exposure conflict.
        """
        by_experiment: dict[str, ExperimentAssignment] = {}
        if self._response is None:
            return []
        for feature_key, feature in self._response.features.items():
            for rule in feature.rules:
                for track in rule.tracks:
                    if not track.result.inExperiment:
                        continue
                    variation_name = self._variant_label(feature, track)
                    if not variation_name:
                        continue
                    by_experiment[feature_key] = ExperimentAssignment(
                        experiment_id=feature_key,
                        experiment_name=track.experiment.key,
                        variation_name=variation_name,
                        variation_id=track.result.variationId,
                        in_experiment=track.result.inExperiment,
                        hash_attribute=track.result.hashAttribute,
                        hash_value=track.result.hashValue,
                        feature_id=track.result.featureId,
                    )
        return list(by_experiment.values())

    @staticmethod
    def _forced_variant_or_none(feature: FeatureDefinition) -> str | None:
        for rule in feature.rules:
            if isinstance(rule.force, str):
                return rule.force
            if rule.force is not None:
                return json.dumps(rule.force)
        return None

    @staticmethod
    def _variant_label(feature: FeatureDefinition, track: TrackData) -> str:
        value = track.result.value
        if value is not None:
            return value if isinstance(value, str) else json.dumps(value)
        resolved = feature.resolved_value()
        if resolved is not None:
            return resolved if isinstance(resolved, str) else json.dumps(resolved)
        if track.result.key is not None:
            return track.result.key
        if track.result.variationId is not None:
            return str(track.result.variationId)
        return ""

    async def aclose(self) -> None:
        await self._client.aclose()
