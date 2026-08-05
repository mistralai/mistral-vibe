from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.experiments.active import DEFAULT_VARIANTS, ExperimentName
    from vibe.core.experiments.client import RemoteEvalClient
    from vibe.core.experiments.manager import ExperimentManager, hash_api_key
    from vibe.core.experiments.models import (
        EvalResponse,
        ExperimentAttributes,
        FeatureDefinition,
        FeatureRule,
        TrackData,
        TrackedExperiment,
        TrackedExperimentResult,
    )

__all__ = [
    "DEFAULT_VARIANTS",
    "EvalResponse",
    "ExperimentAttributes",
    "ExperimentManager",
    "ExperimentName",
    "FeatureDefinition",
    "FeatureRule",
    "RemoteEvalClient",
    "TrackData",
    "TrackedExperiment",
    "TrackedExperimentResult",
    "hash_api_key",
]

_LAZY_MODULES = {
    "RemoteEvalClient": "vibe.core.experiments.client",
    "ExperimentManager": "vibe.core.experiments.manager",
    "hash_api_key": "vibe.core.experiments.manager",
    "EvalResponse": "vibe.core.experiments.models",
    "ExperimentAttributes": "vibe.core.experiments.models",
    "FeatureDefinition": "vibe.core.experiments.models",
    "FeatureRule": "vibe.core.experiments.models",
    "TrackData": "vibe.core.experiments.models",
    "TrackedExperiment": "vibe.core.experiments.models",
    "TrackedExperimentResult": "vibe.core.experiments.models",
    "DEFAULT_VARIANTS": "vibe.core.experiments.active",
    "ExperimentName": "vibe.core.experiments.active",
}


def __getattr__(name: str) -> Any:
    if name in _LAZY_MODULES:
        from importlib import import_module

        module = import_module(_LAZY_MODULES[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
