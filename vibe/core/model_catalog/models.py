"""Client-side types for the ``/model-catalog`` response.

These types mirror the server's response schema but parse tolerantly per
ADR 0014: unknown fields are ignored, missing optional
fields fall back to defaults, and one malformed entry is skipped instead of
failing the whole payload. Only ``id`` and ``label`` identify an entry — a
payload missing either is malformed and dropped.
"""

from __future__ import annotations

from typing import Any

from pydantic import (
    BaseModel,
    Field,
    JsonValue,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from vibe.observability.logging import logger


class ModelCatalogCapabilities(BaseModel):
    completion_chat: bool = False
    function_calling: bool = False
    vision: bool = False


class ModelCatalogParameters(BaseModel):
    """Tunable parameters; null means the client decides."""

    temperature: float | None = None
    reasoning: list[str] | None = None
    auto_compact_threshold: int | None = None


class ModelCatalogEntry(BaseModel):
    """One catalog entry. Server-side every field is present; here every field
    except the identity (``id``, ``label``) has a safe default so an older or
    newer backend cannot break the parse.
    """

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    description: str | None = None
    publisher: str | None = None
    recommended: bool = False
    group: str | None = None
    max_context_length: int | None = None
    parameters: ModelCatalogParameters = Field(default_factory=ModelCatalogParameters)
    capabilities: ModelCatalogCapabilities = Field(
        default_factory=ModelCatalogCapabilities
    )
    # Kept as sent, whatever their shape, so a catalog read back from the cache
    # hides the same entries (see ``_hide_reasons`` in the catalog config layer).
    requires: JsonValue = None
    provider: JsonValue = None

    @field_validator("parameters", mode="before")
    @classmethod
    def _drop_invalid_threshold(cls, parameters: Any, info: ValidationInfo) -> Any:
        """Drop a threshold that is not a positive integer, keeping the entry."""
        if not isinstance(parameters, dict):
            return parameters
        threshold = parameters.get("auto_compact_threshold")
        if threshold is None or (
            isinstance(threshold, int)
            and not isinstance(threshold, bool)
            and threshold > 0
        ):
            return parameters
        logger.warning(
            "Model catalog entry '%s': auto_compact_threshold %r ignored, not a positive integer",
            info.data.get("id"),
            threshold,
        )
        return {**parameters, "auto_compact_threshold": None}


class ModelCatalogResponse(BaseModel):
    models: list[ModelCatalogEntry] = Field(default_factory=list)


def parse_model_catalog_response(data: Any) -> ModelCatalogResponse | None:
    """Parse a catalog payload tolerantly (ADR 0014).

    Unknown fields are ignored and one malformed entry is skipped with a
    warning, never fatal. Returns None only when the top-level payload is not
    a catalog at all — a failure, not an empty catalog, so a corrupt body
    never overwrites a good cache.
    """
    if not isinstance(data, dict):
        return None
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        return None
    models: list[ModelCatalogEntry] = []
    for raw in raw_models:
        try:
            models.append(ModelCatalogEntry.model_validate(raw))
        except ValidationError:
            logger.warning("Skipping malformed model catalog entry: %r", raw)
    return ModelCatalogResponse(models=models)
