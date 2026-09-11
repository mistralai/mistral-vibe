from __future__ import annotations

import hashlib

from vibe.core.experiments import resolve
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.telemetry.types import ExperimentAssignment
from vibe.observability.logging import logger


def hash_api_key(api_key: str) -> str:
    """Stable, anonymous bucketing key derived from the Mistral API key."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:32]


def config_variants_from_response(response: EvalResponse) -> dict[str, object]:
    """Config-layer variants for a cached eval response, computed without network."""
    return resolve.config_variants(response)


class ExperimentManager:
    """Orchestrates the GrowthBook remote eval: fetch, state, and telemetry.

    All decision logic (resolution, config variants, exposures) lives in the
    pure :mod:`vibe.core.experiments.resolve` module; this class only owns the
    transport, the response/attribute state, and logging.
    """

    def __init__(self, client: RemoteEvalClient | None = None) -> None:
        self._client = client if client is not None else RemoteEvalClient()
        self._response: EvalResponse | None = None
        self._attributes: ExperimentAttributes | None = None

    async def initialize(self, attributes: ExperimentAttributes) -> None:
        # Retain the attribute snapshot sent to the proxy for bucketing so
        # telemetry can emit it on the exposure event and the datalake can
        # segment exposures by the same dimensions the proxy assigned on.
        self._attributes = attributes
        response = await self._client.evaluate(attributes)
        if response is None:
            return
        self._response = resolve.filter_to_known(response)
        self._log_resolved_variants("resolved")

    def attributes(self) -> ExperimentAttributes | None:
        """Return the attribute snapshot used for the last eval (telemetry)."""
        return self._attributes

    def set_attributes(self, attributes: ExperimentAttributes) -> None:
        """Set the attribute snapshot without a remote eval.

        Used when there is nothing to evaluate (no Mistral provider) but
        telemetry still needs ``experiment_attributes`` to carry the sentinel
        plan fields (planType/planName = NO_PLAN_DATA).
        """
        self._attributes = attributes

    def hydrate(
        self,
        response: EvalResponse,
        *,
        attributes: ExperimentAttributes | None = None,
        source: str = "session",
    ) -> None:
        # Restore the bucketing snapshot too, not just the eval response: it is
        # what telemetry emits as ``experiment_attributes`` on a resumed session.
        self._attributes = attributes
        self._response = resolve.filter_to_known(response)
        self._log_resolved_variants(f"restored from {source}")

    def export_state(self) -> EvalResponse | None:
        return self._response

    def _log_resolved_variants(self, source: str) -> None:
        resolved = {
            name.value: resolve.variant(self._response, name) for name in ExperimentName
        }
        logger.info(
            "Experiment variants %s (resolved=%s, applied=%s, in_experiment=%s)",
            source,
            resolved,
            self.config_variants(),
            self.assignments(),
        )

    def get_variant_or_none(self, name: ExperimentName) -> object | None:
        return resolve.variant_or_none(self._response, name)

    def get_variant(self, name: ExperimentName) -> object:
        return resolve.variant(self._response, name)

    def config_variants(self) -> dict[str, object]:
        return resolve.config_variants(self._response)

    def assignments(self) -> list[ExperimentAssignment]:
        return resolve.assignments(self._response)

    async def aclose(self) -> None:
        await self._client.aclose()
