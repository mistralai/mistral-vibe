"""Pure, I/O-free resolution of a GrowthBook remote-eval payload.

The proxy performs all targeting and bucketing server-side and returns already
scrubbed rules, so client-side resolution is just: take the winning force rule,
else the feature's ``defaultValue``. This module is deterministic and holds no
state or I/O -- the transport (``client``), persistence (``cache``), and
orchestration (``manager``) concerns live elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
from vibe.core.experiments.models import EvalResponse, FeatureDefinition, TrackData
from vibe.core.telemetry.types import ExperimentAssignment

# Experiments whose value is a whole document: logs and telemetry carry a
# digest of it, not the text.
_DIGESTED_EXPERIMENTS = frozenset({ExperimentName.SYSTEM_PROMPT_BEHAVIOR.value})


def digest(value: str) -> str:
    """A short, stable label for a document-sized variant value."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def loggable_variants(variants: Mapping[str, object]) -> dict[str, object]:
    """``variants`` with each document-sized value replaced by its digest."""
    return {
        name: digest(value)
        if name in _DIGESTED_EXPERIMENTS and isinstance(value, str) and value
        else value
        for name, value in variants.items()
    }


def filter_to_known(response: EvalResponse) -> EvalResponse:
    """Drop features that are not declared in ``ExperimentName``."""
    known = {name.value for name in ExperimentName}
    return EvalResponse(
        features={k: v for k, v in response.features.items() if k in known}
    )


def variant_or_none(
    response: EvalResponse | None, name: ExperimentName
) -> object | None:
    """Resolved value for ``name`` (winning force rule, else defaultValue)."""
    if response is None or name.value not in response.features:
        return None
    return response.features[name.value].resolved_value()


def variant(response: EvalResponse | None, name: ExperimentName) -> object:
    """Resolved value, falling back to Vibe's code-side default."""
    value = variant_or_none(response, name)
    return value if value is not None else DEFAULT_VARIANTS[name]


def config_variants(response: EvalResponse | None) -> dict[str, object]:
    """Typed experiment values allowed to override config layers.

    A value equal to Vibe's baseline is dropped so the low-precedence GrowthBook
    layer expresses only deviations from the schema default.
    """
    result: dict[str, object] = {}
    if response is None:
        return result
    for name in ExperimentName:
        value = variant_or_none(response, name)
        if value is None or value == DEFAULT_VARIANTS[name]:
            continue
        result[name.value] = value
    return result


def assignments(response: EvalResponse | None) -> list[ExperimentAssignment]:
    """Confirmed experiment exposures for telemetry only.

    At most one assignment per experiment_id (last confirmed track wins); the
    dbt exposures model treats one row per (session_id, experiment_id).
    """
    by_experiment: dict[str, ExperimentAssignment] = {}
    if response is None:
        return []
    for feature_key, feature in response.features.items():
        for rule in feature.rules:
            for track in rule.tracks:
                if not track.result.inExperiment:
                    continue
                variation_name = _variant_label(
                    feature, track, digested=feature_key in _DIGESTED_EXPERIMENTS
                )
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


def _variant_label(
    feature: FeatureDefinition, track: TrackData, *, digested: bool = False
) -> str:
    for value in (track.result.value, feature.resolved_value()):
        if value is None:
            continue
        label = value if isinstance(value, str) else json.dumps(value)
        if not digested:
            return label
        if label:
            return digest(label)
        # A blank document is the arm that keeps the shipped text: name it by
        # its GrowthBook variation, so it still counts as an exposure.
        break
    if track.result.key is not None:
        return track.result.key
    if track.result.variationId is not None:
        return str(track.result.variationId)
    return ""
