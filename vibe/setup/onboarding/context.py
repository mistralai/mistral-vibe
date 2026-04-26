from __future__ import annotations

from dataclasses import dataclass
import os
import tomllib
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.config._settings import (
    DEFAULT_ACTIVE_MODEL,
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
)
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger

_ONBOARDING_LIST_ADAPTER = TypeAdapter(list[Any])


def _default_provider_payloads() -> list[dict[str, Any]]:
    return [provider.model_dump(mode="json") for provider in DEFAULT_PROVIDERS]


def _default_model_payloads() -> list[dict[str, Any]]:
    return [model.model_dump(mode="json") for model in DEFAULT_MODELS]


class _OnboardingSnapshot(BaseModel):
    active_model: str = DEFAULT_ACTIVE_MODEL
    providers: list[Any] = Field(default_factory=_default_provider_payloads)
    models: list[Any] = Field(default_factory=_default_model_payloads)


_ONBOARDING_FIELDS = frozenset(_OnboardingSnapshot.model_fields)


def _can_resolve_provider_from_explicit_overrides(
    explicit_overrides: dict[str, Any],
) -> bool:
    return "providers" in explicit_overrides


def _find_env_value(name: str) -> str | None:
    expected_name = name.upper()
    for env_name, value in os.environ.items():
        if env_name.upper() == expected_name:
            return value
    return None


def _load_onboarding_toml_payload() -> dict[str, Any]:
    try:
        harness_files = get_harness_files_manager()
    except RuntimeError:
        return {}

    config_file = harness_files.config_file
    if config_file is None:
        return {}

    try:
        with config_file.open("rb") as file:
            toml_data = tomllib.load(file)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as err:
        raise RuntimeError(f"Invalid TOML in {config_file}: {err}") from err
    except OSError as err:
        raise RuntimeError(f"Cannot read {config_file}: {err}") from err

    return {
        field_name: toml_data[field_name]
        for field_name in _ONBOARDING_FIELDS
        if field_name in toml_data
    }


def _load_onboarding_env_payload_for_fields(
    field_names: frozenset[str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    if (
        "active_model" in field_names
        and (active_model := _find_env_value("VIBE_ACTIVE_MODEL")) is not None
    ):
        payload["active_model"] = active_model
    if (
        "providers" in field_names
        and (providers := _find_env_value("VIBE_PROVIDERS")) is not None
    ):
        payload["providers"] = _ONBOARDING_LIST_ADAPTER.validate_json(providers)
    if (
        "models" in field_names
        and (models := _find_env_value("VIBE_MODELS")) is not None
    ):
        payload["models"] = _ONBOARDING_LIST_ADAPTER.validate_json(models)

    return payload


def _explicit_onboarding_overrides(**overrides: Any) -> dict[str, Any]:
    return {
        field_name: value
        for field_name, value in overrides.items()
        if field_name in _ONBOARDING_FIELDS
    }


def _build_onboarding_snapshot_payload(**overrides: Any) -> dict[str, Any]:
    explicit_overrides = _explicit_onboarding_overrides(**overrides)
    payload = _OnboardingSnapshot().model_dump()

    if explicit_overrides.keys() >= _ONBOARDING_FIELDS:
        payload.update(explicit_overrides)
        return payload

    try:
        payload.update(_load_onboarding_toml_payload())
    except RuntimeError:
        if not _can_resolve_provider_from_explicit_overrides(explicit_overrides):
            raise
    try:
        payload.update(
            _load_onboarding_env_payload_for_fields(
                _ONBOARDING_FIELDS.difference(explicit_overrides)
            )
        )
    except (ValidationError, ValueError):
        if not _can_resolve_provider_from_explicit_overrides(explicit_overrides):
            raise
    payload.update(explicit_overrides)
    return payload


def _validated_payloads[PayloadConfig: ModelConfig | ProviderConfig](
    payloads: list[Any], model_type: type[PayloadConfig]
) -> list[PayloadConfig]:
    validated_payloads: list[PayloadConfig] = []
    for payload in payloads:
        if isinstance(payload, model_type):
            validated_payloads.append(payload)
            continue
        if not isinstance(payload, dict):
            continue
        try:
            validated_payloads.append(model_type.model_validate(payload))
        except (ValidationError, ValueError):
            continue
    return validated_payloads


def _resolve_provider(
    *, active_model: str, snapshot: _OnboardingSnapshot
) -> ProviderConfig:
    providers_by_name: dict[str, ProviderConfig] = {}
    for provider in _validated_payloads(snapshot.providers, ProviderConfig):
        providers_by_name.setdefault(provider.name, provider)

    models = _validated_payloads(snapshot.models, ModelConfig)

    for model_alias in (active_model, DEFAULT_ACTIVE_MODEL):
        for model in models:
            if model.alias != model_alias:
                continue
            if provider := providers_by_name.get(model.provider):
                return provider

    for model in models:
        if provider := providers_by_name.get(model.provider):
            return provider

    if len(providers_by_name) == 1:
        return next(iter(providers_by_name.values()))

    return DEFAULT_PROVIDERS[0]


@dataclass(frozen=True)
class OnboardingContext:
    provider: ProviderConfig

    @property
    def supports_browser_sign_in(self) -> bool:
        return self.provider.supports_browser_sign_in

    @classmethod
    def from_config(cls, config: VibeConfig) -> OnboardingContext:
        return cls(provider=config.get_provider_for_model(config.get_active_model()))

    @classmethod
    def load(cls, **overrides: Any) -> OnboardingContext:
        try:
            snapshot = _OnboardingSnapshot.model_validate(
                _build_onboarding_snapshot_payload(**overrides)
            )
            return cls(
                provider=_resolve_provider(
                    active_model=snapshot.active_model, snapshot=snapshot
                )
            )
        except (RuntimeError, ValidationError, ValueError):
            logger.warning(
                "Onboarding config fallback activated; using defaults", exc_info=True
            )
            return cls.from_config(VibeConfig.model_construct())
