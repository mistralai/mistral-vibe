from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from vibe.config_values import THINKING_LEVELS, ThinkingLevel
from vibe.core.config.fingerprint import create_dict_fingerprint
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.models import ModelConfig
from vibe.core.config.types import EMPTY_CONFIG_SNAPSHOT, LayerConfigSnapshot
from vibe.observability.logging import logger

if TYPE_CHECKING:
    # Type-only: the catalog package pulls its HTTP client, which must stay
    # off the config-stack import path.
    from vibe.core.config.orchestrator import ConfigOrchestrator
    from vibe.core.config.vibe_schema import VibeConfigSchema
    from vibe.core.model_catalog.models import ModelCatalogEntry, ModelCatalogResponse

# Always defined: providers union-merge by name, so no layer can remove it.
_CATALOG_PROVIDER = "mistral"

# The catalog lists ``reasoning_effort`` wire values; "none" is the wire value
# of the "off" level, every other value names its level.
_WIRE_NONE = "none"

_CATALOG_FIELDS = frozenset({
    "name",
    "provider",
    "alias",
    "display_name",
    "supports_images",
    "max_context_length",
    "temperature",
    "thinking_levels",
    "thinking",
})


class ModelCatalogLayer(ConfigLayer[RawConfig]):
    """The fetched model catalog as config, just above the built-in defaults.

    Read-only and session-resolved like the GrowthBook layer: the layer never
    saves to ``config.toml``, and every layer above the built-in defaults
    outranks it.
    """

    NAME = "model_catalog"

    def __init__(self) -> None:
        super().__init__(name=self.NAME)
        self._catalog: ModelCatalogResponse | None = None
        self._snapshot = EMPTY_CONFIG_SNAPSHOT

    @property
    def catalog(self) -> ModelCatalogResponse | None:
        return self._catalog

    def set_catalog(self, catalog: ModelCatalogResponse | None) -> None:
        # Mapped once per catalog, not on every config rebuild, so a skipped
        # entry is logged once.
        self._catalog = catalog
        self._snapshot = _catalog_snapshot(catalog)

    async def _check_trust(self) -> bool:
        return True

    async def _build_config_snapshot(self) -> LayerConfigSnapshot:
        return self._snapshot

    async def _save_to_store(self, _next_config: RawConfig) -> str:
        raise NotImplementedError("ModelCatalogLayer is read-only")


def _catalog_snapshot(catalog: ModelCatalogResponse | None) -> LayerConfigSnapshot:
    if catalog is None:
        return EMPTY_CONFIG_SNAPSHOT
    entries = [
        (entry, payload)
        for entry in catalog.models
        if (payload := _catalog_model_payload(entry)) is not None
    ]
    if not entries:
        return EMPTY_CONFIG_SNAPSHOT
    data: dict[str, Any] = {"models": {entry.id: payload for entry, payload in entries}}
    if aliases := {entry.id: entry.aliases for entry, _ in entries if entry.aliases}:
        data["catalog_model_aliases"] = aliases
    # A dropped recommended entry leaves no recommendation: the local default
    # applies, rather than a model the catalog did not pick.
    if recommended := next((entry.id for entry, _ in entries if entry.recommended), ""):
        data["catalog_recommended_model"] = recommended
    return LayerConfigSnapshot(data=data, fingerprint=create_dict_fingerprint(data))


def _catalog_model_payload(entry: ModelCatalogEntry) -> dict[str, Any] | None:
    """The model config an entry contributes, or None to drop the entry.

    Dropped: no function calling (the agent loop needs tool calls), or values
    the model config rejects (one bad entry must not fail the config, ADR 0014).
    A null value maps to the field default, so an entry never inherits a
    built-in model's value under the same ID.
    """
    if not entry.capabilities.function_calling:
        logger.info("Model catalog entry '%s' skipped: no function calling", entry.id)
        return None
    values: dict[str, Any] = {
        "name": entry.id,
        "provider": _CATALOG_PROVIDER,
        "alias": entry.id,
        "display_name": entry.label,
        "supports_images": entry.capabilities.vision,
        "max_context_length": entry.max_context_length,
    }
    if entry.parameters.temperature is not None:
        values["temperature"] = entry.parameters.temperature
    if entry.parameters.reasoning is not None:
        values["thinking_levels"] = _thinking_levels(
            entry.id, entry.parameters.reasoning
        )
    try:
        model = ModelConfig.model_validate(values)
    except ValidationError:
        logger.warning("Model catalog entry '%s' skipped: invalid values", entry.id)
        return None
    # A curated set starts on its own default level; an uncurated model keeps
    # the field default, as a locally configured one does.
    if entry.parameters.reasoning is not None:
        model = model.model_copy(update={"thinking": model.default_thinking})
    return model.model_dump(include=set(_CATALOG_FIELDS))


def _thinking_levels(model_id: str, reasoning: list[str]) -> list[ThinkingLevel]:
    levels: list[ThinkingLevel] = []
    for value in reasoning:
        level = "off" if value == _WIRE_NONE else value
        if level not in THINKING_LEVELS:
            logger.warning(
                "Model catalog entry '%s': unknown reasoning value '%s' ignored",
                model_id,
                value,
            )
            continue
        levels.append(cast(ThinkingLevel, level))
    return levels


async def apply_cached_model_catalog(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> None:
    """Apply the last cached catalog to config before first render.

    Fail-open: a cached catalog the config cannot be built from is ignored.
    """
    config = orchestrator.config
    if not config.experimental_enable_model_catalog:
        return
    # Imported here for the same reason as the type-only imports above.
    from vibe.core.model_catalog import load_cached_model_catalog

    cached = load_cached_model_catalog(config)
    if cached is None:
        return
    try:
        changed = await apply_model_catalog(orchestrator, cached)
    except Exception:
        logger.exception("Failed to apply the cached model catalog to config")
        return
    if changed:
        logger.info("Model catalog applied from cache: %d models", len(cached.models))


async def apply_model_catalog(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], catalog: ModelCatalogResponse
) -> bool:
    """Rebuild the orchestrator's config on *catalog*; False when unchanged.

    A fetch usually returns the cached catalog: skipping it spares a reload of
    every layer and a client refresh on each session start. A failed rebuild
    restores the previous catalog, so the layer never holds one the config
    could not be built from.
    """
    layer = _catalog_layer(orchestrator)
    previous = layer.catalog
    if catalog == previous:
        return False
    layer.set_catalog(catalog)
    try:
        await orchestrator.reload()
    except BaseException:
        layer.set_catalog(previous)
        # The reload may have cached the rejected catalog before failing; a
        # later non-forcing rebuild would bring it back.
        await layer.invalidate_cache()
        raise
    return True


def _catalog_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> ModelCatalogLayer:
    layer = orchestrator.get_layer(ModelCatalogLayer.NAME)
    if not isinstance(layer, ModelCatalogLayer):
        raise TypeError(f"Layer {ModelCatalogLayer.NAME!r} is not the catalog layer")
    return layer
