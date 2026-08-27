from __future__ import annotations

from dataclasses import dataclass
import os
import tomllib
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, ValidationError

from vibe.core.config import (
    DEFAULT_ACTIVE_MODEL_CONFIG,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MODELS,
    DEFAULT_PROVIDERS,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
    ModelConfig,
    ProviderConfig,
    VibeConfigSchema,
)
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.config.models import normalize_model_configs
from vibe.observability.logging import logger

_ONBOARDING_LIST_ADAPTER = TypeAdapter(list[Any])


def _normalize_origin(value: str) -> str:
    origin = value.strip()
    if "://" not in origin:
        origin = f"https://{origin}"
    return origin.rstrip("/")


_HTTP_URL_ADAPTER = TypeAdapter(HttpUrl)


def is_valid_custom_domain(value: str) -> bool:
    # http:// is accepted on purpose so users can target local auth gateways
    # (e.g. http://localhost:8080); _normalize_origin only upgrades scheme-less
    # inputs to https://, an explicit scheme is always respected.
    origin = value.strip()
    if "://" not in origin and ":/" in origin:
        return False
    try:
        _HTTP_URL_ADAPTER.validate_python(_normalize_origin(origin))
    except ValidationError:
        return False
    return True


def resolve_browser_auth_urls(domain: str) -> tuple[str, str]:
    base = _normalize_origin(domain)
    api = f"{base}/api"
    return base, api


def is_likely_mistral_private_cloud_domain(domain: str) -> bool:
    """Heuristic: a Mistral-hosted subdomain that is not the default auth host.

    Private-cloud Studio redirects users to a custom `*.mistral.ai` URL, but
    Vibe CLI browser sign-in for Mistral-hosted accounts uses `console.mistral.ai`.
    Surfacing this lets the wizard warn users who paste their Studio URL.
    """
    host = urlparse(_normalize_origin(domain)).hostname or ""
    return (
        host != "console.mistral.ai"
        and host.startswith("console.")
        and host.endswith(".mistral.ai")
    )


def _default_provider_payloads() -> list[dict[str, Any]]:
    return [provider.model_dump(mode="json") for provider in DEFAULT_PROVIDERS]


def _default_model_payloads() -> list[dict[str, Any]]:
    return [model.model_dump(mode="json") for model in DEFAULT_MODELS]


class _OnboardingSnapshot(BaseModel):
    active_model: str = DEFAULT_ACTIVE_MODEL_CONFIG.alias
    theme: str = DEFAULT_THEME
    vibe_base_url: str = DEFAULT_VIBE_BASE_URL
    console_base_url: str = DEFAULT_CONSOLE_BASE_URL
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

    payload = {
        field_name: toml_data[field_name]
        for field_name in _ONBOARDING_FIELDS
        if field_name in toml_data
    }
    if "models" in payload:
        payload["models"] = _normalize_onboarding_model_payloads(payload["models"])
    return payload


def _normalize_onboarding_model_payloads(value: Any) -> Any:
    normalized = normalize_model_configs(value)
    if isinstance(normalized, dict):
        return list(normalized.values())
    return normalized


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
    if (
        "vibe_base_url" in field_names
        and (vibe_base_url := _find_env_value("VIBE_VIBE_BASE_URL")) is not None
    ):
        payload["vibe_base_url"] = vibe_base_url
    if (
        "console_base_url" in field_names
        and (console_base_url := _find_env_value("VIBE_CONSOLE_BASE_URL")) is not None
    ):
        payload["console_base_url"] = console_base_url
    if "theme" in field_names and (theme := _find_env_value("VIBE_THEME")) is not None:
        payload["theme"] = theme

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

    for model_alias in (active_model, DEFAULT_ACTIVE_MODEL_CONFIG.alias):
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
    vibe_base_url: str = DEFAULT_VIBE_BASE_URL
    console_base_url: str = DEFAULT_CONSOLE_BASE_URL
    theme: str = DEFAULT_THEME

    @property
    def supports_browser_sign_in(self) -> bool:
        return self.provider.supports_browser_sign_in

    @classmethod
    def from_config(cls, config: VibeConfigSchema) -> OnboardingContext:
        return cls(
            provider=config.get_active_provider(),
            vibe_base_url=config.vibe_base_url,
            console_base_url=config.console_base_url,
            theme=config.theme,
        )

    @classmethod
    def load(cls, **overrides: Any) -> OnboardingContext:
        try:
            snapshot = _OnboardingSnapshot.model_validate(
                _build_onboarding_snapshot_payload(**overrides)
            )
            return cls(
                provider=_resolve_provider(
                    active_model=snapshot.active_model, snapshot=snapshot
                ),
                vibe_base_url=snapshot.vibe_base_url,
                console_base_url=snapshot.console_base_url,
                theme=snapshot.theme,
            )
        except (RuntimeError, ValidationError, ValueError):
            logger.warning(
                "Onboarding config fallback activated; using defaults", exc_info=True
            )
            return cls.from_config(VibeConfigSchema.model_construct())
