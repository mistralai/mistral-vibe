from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping, Sequence
from enum import StrEnum
import functools
import json
import os
from pathlib import Path
import tomllib
import typing
from typing import Annotated, Any, ClassVar, Self

from dotenv import dotenv_values
from pydantic import (
    AfterValidator,
    BeforeValidator,
    Field,
    PrivateAttr,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from vibe.core.agents.models import REMOVED_BUILTIN_AGENTS, BuiltinAgentName
from vibe.core.config._defaults import (
    DEFAULT_API_RETRY_MAX_ELAPSED_TIME,
    DEFAULT_API_STREAM_IDLE_TIMEOUT,
    DEFAULT_API_TIMEOUT,
    DEFAULT_COMPACTION_REASONING_LIMIT_TOKENS,
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_MISTRAL_API_ENV_KEY,
    DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
    DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
    DEFAULT_MISTRAL_SERVER_URL,
    DEFAULT_OUTPUT_LIMIT_RETRIES,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
    SHELL_TOOLS,
)

# DEFAULT_LOG_LEVEL is not imported here to avoid a circular dependency
# (vibe_schema.py imports from vibe.observability.logging). The constant
# lives in vibe.config_values.
from vibe.core.config._model_aliases import build_alias_index, warn_ignored_alias
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.models import (
    ACTIVE_MODEL_SELECTOR,
    THINKING_LEVELS,
    ConnectorConfig,
    ExperimentsConfig,
    MCPServer,
    MissingAPIKeyError,
    ModelConfig,
    OtelRedactionMode,
    ProjectContextConfig,
    ProviderConfig,
    SessionLoggingConfig,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSModelConfig,
    TTSProviderConfig,
    UtilityFeature,
    UtilityModelsConfig,
    normalize_model_configs,
    refuse_zero_auto_compact_threshold,
    serialize_model_configs,
)
from vibe.core.config.schema import (
    ConfigSchema,
    WithConcatMerge,
    WithDeepMerge,
    WithReplaceMerge,
    WithShallowMerge,
    WithUnionMerge,
)
from vibe.core.config.shell_timeouts import check_shell_timeout_pair
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.core.prompts import (
    SystemPrompt,
    UtilityPrompt,
    load_prompt,
    load_system_prompt,
)
from vibe.core.types import Backend
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger
from vibe.permissions import OutsideWorkdirPolicy
from vibe.utils.api_keys import resolve_api_key
from vibe.utils.keyring import keyring_read_failure

# Every shell tool scopes a grant with ``build_session_pattern``, which appends
# " *" to mean "any arguments". Allowlists are matched by prefix equality rather
# than as globs, so the wildcard has to come back off before it is persisted or
# the stored entry matches nothing and the next session prompts again. Windows
# spells the shell ``powershell``/``git_bash``, so keying this on ``bash`` alone
# left permanent approvals silently inert there.
_SESSION_PATTERN_WILDCARD_TOOLS = frozenset({"bash", "git_bash", "powershell"})


def _strip_session_pattern_wildcard(pattern: str) -> str:
    if pattern.endswith(" *"):
        return pattern[:-2]
    return pattern


def _harness_files_from_context(context: Any) -> HarnessFilesManager | None:
    if not isinstance(context, Mapping):
        return None
    harness_files = context.get("harness_files")
    return harness_files if isinstance(harness_files, HarnessFilesManager) else None


class EnvValueOrigin(StrEnum):
    """Where a value Vibe itself wrote into ``os.environ`` came from."""

    VIBE_HOME_ENV_FILE = "vibe_home_env_file"
    OS_KEYRING = "os_keyring"


# The origin of every value Vibe itself wrote into ``os.environ``, keyed by
# name. Process-global by design: the record answers "did this environment
# value come from Vibe or from the parent shell?", which only the process
# that performed the write can know. Read it through
# :func:`env_value_origin`.
_ENV_VALUE_ORIGINS: dict[str, tuple[EnvValueOrigin, str]] = {}


def record_env_value_origin(env_key: str, value: str, origin: EnvValueOrigin) -> None:
    """Record where a value Vibe wrote into the environment came from.

    Writers are the ``.env`` loader and credential persistence (onboarding
    mirrors a stored key into ``os.environ`` so the running process can use
    it at once).
    """
    _ENV_VALUE_ORIGINS[env_key] = (origin, value)


def forget_env_value_origin(env_key: str) -> None:
    """Drop the record for ``env_key`` (the value was removed)."""
    _ENV_VALUE_ORIGINS.pop(env_key, None)


def env_value_origin(env_key: str) -> EnvValueOrigin | None:
    """The recorded origin of ``env_key``'s value, or ``None``.

    A record matches only while the environment still holds the exact value
    that was written; anything else reads as the parent shell's.
    """
    recorded = _ENV_VALUE_ORIGINS.get(env_key)
    if recorded is None:
        return None
    origin, value = recorded
    return origin if os.environ.get(env_key) == value else None


def load_dotenv_values(
    env_path: Path = GLOBAL_ENV_FILE.path,
    environ: MutableMapping[str, str] = os.environ,
) -> None:
    # We allow FIFO path to support some environment management solutions (e.g. https://developer.1password.com/docs/environments/local-env-file/)
    if not env_path.is_file() and not env_path.is_fifo():
        return

    env_vars = dotenv_values(env_path)
    # Only injections into the real process environment are recorded: a caller
    # passing a private mapping (tests) did not change where this process's
    # credentials come from.
    record_injection = environ is os.environ
    for key, value in env_vars.items():
        if not value:
            continue
        if environ.get(key):
            # An explicit non-empty process/shell value wins over the .env file.
            continue
        environ[key] = value
        if record_injection:
            record_env_value_origin(key, value, EnvValueOrigin.VIBE_HOME_ENV_FILE)


DEFAULT_PROVIDERS = [
    ProviderConfig(
        name="mistral",
        api_base=f"{DEFAULT_MISTRAL_SERVER_URL}/v1",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
        browser_auth_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_BASE_URL,
        browser_auth_api_base_url=DEFAULT_MISTRAL_BROWSER_AUTH_API_BASE_URL,
        backend=Backend.MISTRAL,
    ),
    ProviderConfig(
        name="llamacpp",
        api_base="http://127.0.0.1:8080/v1",
        api_key_env_var="",  # NOTE: if you wish to use --api-key in llama-server, change this value
    ),
]

DEFAULT_ACTIVE_MODEL_CONFIG = ModelConfig(
    name="mistral-vibe-cli-latest",
    provider="mistral",
    alias="mistral-medium-3.5",
    display_name="Mistral Medium 3.5",
    temperature=1.0,
    input_price=1.5,
    output_price=7.5,
    cached_input_price=0.15,
    thinking="high",
    supports_images=True,
    thinking_levels=["off", "high"],
    max_context_length=262144,  # 256k window per the model card
)

DEFAULT_MODELS = [DEFAULT_ACTIVE_MODEL_CONFIG]

# Sentinel ``active_model`` value meaning "not pinned": the config resolves it to
# the default model (see ``get_active_model``). Kept distinct from pinning the
# alias that happens to be the current default, which is a deliberate choice.
UNPINNED_ACTIVE_MODEL = ""

DEFAULT_TRANSCRIBE_PROVIDERS = [
    TranscribeProviderConfig(
        name="mistral",
        api_base="wss://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG = TranscribeModelConfig(
    name="voxtral-mini-transcribe-realtime-2602",
    provider="mistral",
    alias="voxtral-realtime",
)

DEFAULT_TRANSCRIBE_MODELS = [DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG]

DEFAULT_TTS_PROVIDERS = [
    TTSProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var=DEFAULT_MISTRAL_API_ENV_KEY,
    )
]

DEFAULT_ACTIVE_TTS_MODEL_CONFIG = TTSModelConfig(
    name="voxtral-mini-tts-latest", provider="mistral", alias="voxtral-tts"
)

DEFAULT_TTS_MODELS = [DEFAULT_ACTIVE_TTS_MODEL_CONFIG]


def get_persisted_config() -> dict[str, Any]:
    file = get_harness_files_manager().config_file
    if file is None:
        return {}
    try:
        with file.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as e:
        raise RuntimeError(f"Invalid TOML in {file}: {e}") from e
    except OSError as e:
        raise RuntimeError(f"Cannot read {file}: {e}") from e


def _unique_by(key: str) -> Callable[[list[Any]], list[Any]]:
    def check(items: list[Any]) -> list[Any]:
        seen: set[str] = set()
        for item in items:
            value = getattr(item, key)
            if value in seen:
                raise ValueError(f"Duplicate {key} {value!r}; must be unique")
            seen.add(value)
        return items

    return check


def _non_empty(items: list[Any]) -> list[Any]:
    if not items:
        raise ValueError(
            "No models are configured. Define at least one model under [[models]]."
        )
    return items


def _defines_model(payload: object) -> bool:
    """Whether a ``models`` entry is a whole model, not a setting on another one.

    Without ``name`` and ``provider`` the entry cannot validate on its own: it
    adjusts a model some layer defines, like ``[models.<id>]`` or a field-only
    write.
    """
    return not isinstance(payload, Mapping) or {"name", "provider"} <= payload.keys()


def _without_orphan_model_overrides(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop model overrides that no layer completes, and name them.

    A merged entry that is still not a whole model (``_defines_model``) only
    adjusts a model nothing defines (a retired catalog model, an ended
    experiment, a typo); it cannot validate, so it is left out for the rest of
    the config to load, and applies again if the model returns.
    """
    models = data.get("models")
    if not isinstance(models, Mapping):
        return data, []
    orphans = [
        alias for alias, payload in models.items() if not _defines_model(payload)
    ]
    if not orphans:
        return data, []
    kept = {alias: payload for alias, payload in models.items() if alias not in orphans}
    return {**data, "models": kept}, orphans


def _without_blank_thinking_levels(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Drop thinking values that are not words, and say which.

    Any word is a level, but a blank or a number is none: one such typo must
    not stop the config from loading, or the session could not open, and the
    pickers that would fix it with it. The model then runs at its default level
    until the user picks one.
    """
    models = data.get("models")
    if not isinstance(models, Mapping):
        return data, []
    warnings: list[str] = []
    kept: dict[str, Any] = {}
    for alias, payload in models.items():
        if not isinstance(payload, Mapping):
            kept[alias] = payload
            continue
        payload = dict(payload)
        if "thinking" in payload and not _is_word(thinking := payload["thinking"]):
            del payload["thinking"]
            warnings.append(
                f"Model '{alias}': thinking {thinking!r} is not a level; using the "
                "model's default. Pick another thinking level."
            )
        levels = payload.get("thinking_levels")
        if isinstance(levels, list) and not all(map(_is_word, levels)):
            words = [level for level in levels if _is_word(level)]
            if words:
                payload["thinking_levels"] = words
            else:
                del payload["thinking_levels"]
            ignored = ", ".join(repr(level) for level in levels if not _is_word(level))
            warnings.append(f"Model '{alias}': thinking_levels {ignored} ignored.")
        kept[alias] = payload
    if not warnings:
        return data, []
    return {**data, "models": kept}, warnings


def _is_word(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _expand_paths(v: Any) -> list[Path]:
    if not v:
        return []
    return [Path(p).expanduser().resolve() for p in v]


def _normalize_tool_configs(v: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(v, dict):
        return {}
    return {name: cfg if isinstance(cfg, dict) else {} for name, cfg in v.items()}


def _reject_invalid_shell_timeouts(
    tools: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """A shell tool's `default_timeout` and `max_timeout_seconds` are finite
    numbers of seconds above 0, and the default is not above the maximum. A
    default set alone runs under Vibe's own maximum when within it; any other
    partial pair is a configuration error, and a default above the maximum is
    not lowered quietly.

    Checked here so a config fails at load; the unified harness's own rule is
    `bash_timeout` in the local harness's `vibe/_shell_tools.py`.
    """
    for name in SHELL_TOOLS:
        check_shell_timeout_pair(name, tools.get(name, {}))
    return tools


def _coerce_routed_model_config(v: str) -> ModelConfig | None:
    try:
        return ModelConfig.model_validate_json(v)
    except ValidationError:
        return None


def _coerce_routed_extra_models(v: Any) -> list[ModelConfig]:
    """Parse the GrowthBook-supplied extra-models payload, failing open.

    The value arrives as a JSON-array string (like every other GrowthBook-mapped
    config value). Invalid entries are dropped rather than raising so a malformed
    A/B payload can never brick config loading.
    """
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(v, list):
        return []
    coerced: list[ModelConfig] = []
    for item in v:
        try:
            coerced.append(
                item
                if isinstance(item, ModelConfig)
                else ModelConfig.model_validate(item)
            )
        except ValidationError:
            continue
    return coerced


_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


def _normalize_log_level(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    normalized = value.strip().upper()
    if normalized not in _LOG_LEVELS:
        raise ValueError(
            f"Invalid log level {value!r}; expected one of {sorted(_LOG_LEVELS)}"
        )
    return normalized


@functools.cache
def _mistral_efforts() -> frozenset[str]:
    """The efforts the Mistral SDK puts on the wire; it leaves any other out."""
    from mistralai.client.models import ReasoningEffort

    return frozenset(
        value
        for member in typing.get_args(ReasoningEffort)
        for value in typing.get_args(member)
        if isinstance(value, str)
    )


def _mistral_thinking_levels() -> list[str]:
    """The SDK's efforts as Vibe names them: the standard levels in order, then its own."""
    efforts = {"off" if effort == "none" else effort for effort in _mistral_efforts()}
    standard = [level for level in THINKING_LEVELS if level in efforts]
    return [*standard, *sorted(efforts - set(standard))]


class VibeConfigSchema(ConfigSchema):
    # Fields a runtime layer populates (model catalog, GrowthBook, managed
    # shell), never the user's files. The settings UI hides them; declare a new
    # one here, not in a second list.
    RUNTIME_INJECTED_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "catalog_model_aliases",
        "catalog_recommended_model",
        "managed_shell_tools_enabled",
        "routed_default_model",
        "routed_model_config",
        "routed_extra_models",
        "smart_approve_available",
        "smart_approve_default",
    })

    _validation_warnings: list[str] = PrivateAttr(default_factory=list)
    _harness_files: HarnessFilesManager | None = PrivateAttr(default=None)
    # The pin the fallback replaced, so validate_merged can skip the orphan
    # notice for the same alias.
    _missing_pin: str = PrivateAttr(default="")

    @property
    def validation_warnings(self) -> tuple[str, ...]:
        return tuple(self._validation_warnings)

    @classmethod
    def defines_entry(cls, key: str, payload: Any) -> bool:
        return key != "models" or _defines_model(payload)

    def check_layer_keys(self, layers: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        from vibe.core.config._setting_keys import (
            UnusedConfigKeysError,
            check_strict_config_env,
            find_unused_keys,
            notice,
        )

        check_strict_config_env()
        issues = find_unused_keys(type(self), layers)
        if not issues:
            return
        if self.strict_config:
            raise UnusedConfigKeysError(issues)
        for issue in issues:
            logger.warning(
                "Config setting ignored: %s (%s)", issue.describe(), issue.layer
            )
        self._validation_warnings.extend(notice(issues))

    @classmethod
    def validate_merged(
        cls,
        data: dict[str, Any],
        *,
        origins: dict[str, str],
        defined_entries: dict[str, dict[str, tuple[str, ...]]],
        context: Mapping[str, Any] | None = None,
    ) -> Self:
        data, orphans = _without_orphan_model_overrides(data)
        data, thinking_warnings = _without_blank_thinking_levels(data)
        config = super().validate_merged(
            data, origins=origins, defined_entries=defined_entries, context=context
        )
        config._harness_files = _harness_files_from_context(context)
        for message in thinking_warnings:
            logger.warning("%s", message)
            config._validation_warnings.append(message)
        # The missing-pin warning already named a pinned orphan: one notice
        # per model, not one per config value. Only the fallback's own alias
        # is skipped — a pin that resolves through a catalog alias fires no
        # fallback, and its orphaned override keeps its warning.
        for alias in orphans:
            if alias == config._missing_pin:
                continue
            message = (
                f"Settings for model '{alias}' are ignored: no such model is available."
            )
            logger.warning("%s", message)
            config._validation_warnings.append(message)
        # Model validators run before origins are assigned.
        if (
            config.origin_of("allowed_models") == AdminConfigLayer.NAME
            and config.allowed_models
            and not config.available_models()
        ):
            raise ValueError(
                "Admin allowed_models matches none of the configured models."
            )
        if config.origin_of("auto_compact_threshold") != AdminConfigLayer.NAME:
            return config

        models = {
            alias: model.model_copy(
                update={"auto_compact_threshold": config.auto_compact_threshold}
            )
            for alias, model in config.models.items()
        }
        object.__setattr__(config, "models", models)
        return config

    # Models
    active_model: Annotated[str, WithReplaceMerge()] = UNPINNED_ACTIVE_MODEL
    # Experiment-routed default model alias, populated at runtime by the
    # GrowthBook layer. It only takes effect when ``active_model`` is unpinned.
    routed_default_model: Annotated[str, WithReplaceMerge()] = ""
    # Full definition of ``routed_default_model``, also supplied at runtime by the GrowthBook layer.
    routed_model_config: Annotated[
        ModelConfig | None,
        WithReplaceMerge(),
        BeforeValidator(_coerce_routed_model_config),
    ] = None
    # Other names for catalog models (model ID -> names), populated at runtime
    # by the model catalog layer: a pin on one of them selects that model.
    catalog_model_aliases: Annotated[dict[str, list[str]], WithReplaceMerge()] = Field(
        default_factory=dict
    )
    # The catalog's recommended model ID, populated at runtime by the model
    # catalog layer: the default below a live GrowthBook routed default.
    catalog_recommended_model: Annotated[str, WithReplaceMerge()] = ""
    # Extra models injected into the dropdown at runtime by the GrowthBook layer,
    # for exposure/rollout without changing the default. Unlike
    # ``routed_model_config``, these never influence ``resolve_default_model_alias``.
    routed_extra_models: Annotated[
        list[ModelConfig],
        WithReplaceMerge(),
        BeforeValidator(_coerce_routed_extra_models),
    ] = Field(default_factory=list)
    providers: Annotated[list[ProviderConfig], WithUnionMerge(merge_key="name")] = (
        Field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    )
    models: Annotated[
        dict[str, ModelConfig],
        # Keyed by alias internally so per-model patches can deep-merge.
        # Sparse default-model overrides are completed by DefaultConfigLayer at
        # merge time; here we only normalize the list / alias map into the map shape.
        WithDeepMerge(),
        BeforeValidator(normalize_model_configs),
        AfterValidator(_non_empty),
    ] = Field(default_factory=lambda: normalize_model_configs(DEFAULT_MODELS))
    allowed_models: Annotated[list[str], WithReplaceMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of model names/patterns to allow. If set, only these"
            " models are selectable. An empty list allows all configured models."
            " Supports glob patterns (e.g., 'mistral-*') and regex with 're:' prefix."
        ),
    )
    compaction_model: Annotated[ModelConfig | None, WithShallowMerge()] = None
    vision_model: Annotated[ModelConfig | None, WithShallowMerge()] = Field(
        default=None,
        description=(
            "Vision-capable model that describes attached images for an active"
            " model that cannot see them. Only needed to override the default,"
            " which is any vision-capable model on the active model's own"
            " provider; set this to reach a different provider."
        ),
    )
    # Shallow: a layer setting one feature must not reset the other.
    utility_models: Annotated[UtilityModelsConfig, WithShallowMerge()] = Field(
        default_factory=UtilityModelsConfig,
        description=(
            "Per-feature model for secondary completions (session titles, the"
            " smart-approve classifier). Each value is a model alias from 'models'"
            f" or '{ACTIVE_MODEL_SELECTOR}' to use the session's active model."
            " Unset leaves the feature on automatic selection."
        ),
    )
    auto_compact_threshold: Annotated[int | None, WithReplaceMerge()] = Field(
        default=None,
        gt=0,
        description=(
            "Token count before automatic compaction for models without their "
            "own threshold. Unset uses each model's default, derived from its "
            "context window. A threshold outside the range the window allows is "
            "refused with an error that names the range."
        ),
    )
    active_transcribe_model: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_ACTIVE_TRANSCRIBE_MODEL_CONFIG.alias
    )
    transcribe_providers: Annotated[
        list[TranscribeProviderConfig], WithUnionMerge(merge_key="name")
    ] = Field(default_factory=lambda: list(DEFAULT_TRANSCRIBE_PROVIDERS))
    transcribe_models: Annotated[
        list[TranscribeModelConfig],
        WithUnionMerge(merge_key="alias"),
        AfterValidator(_unique_by("alias")),
    ] = Field(default_factory=lambda: list(DEFAULT_TRANSCRIBE_MODELS))
    active_tts_model: Annotated[str, WithReplaceMerge()] = (
        DEFAULT_ACTIVE_TTS_MODEL_CONFIG.alias
    )
    tts_providers: Annotated[
        list[TTSProviderConfig], WithUnionMerge(merge_key="name")
    ] = Field(default_factory=lambda: list(DEFAULT_TTS_PROVIDERS))
    tts_models: Annotated[
        list[TTSModelConfig],
        WithUnionMerge(merge_key="alias"),
        AfterValidator(_unique_by("alias")),
    ] = Field(default_factory=lambda: list(DEFAULT_TTS_MODELS))

    # Tools
    tools: Annotated[
        dict[str, dict[str, Any]],
        WithDeepMerge(),
        BeforeValidator(_normalize_tool_configs),
        AfterValidator(_reject_invalid_shell_timeouts),
    ] = Field(default_factory=dict)
    outside_workdir: Annotated[OutsideWorkdirPolicy, WithReplaceMerge()] = Field(
        default=OutsideWorkdirPolicy.ASK,
        description=(
            "What happens when a file or shell tool call reaches a path outside the"
            " working directory and its added directories: 'ask' prompts for"
            " approval, 'deny' refuses the call and tells the agent why, so it keeps"
            " working. Allowlisted paths are unaffected. Override per tool with"
            " 'outside_workdir' under [tools.<name>]."
        ),
    )
    enabled_tools: Annotated[list[str], WithReplaceMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of tool names/patterns to enable. If set, only these"
            " tools will be active. Supports glob patterns (e.g., 'serena_*') and"
            " regex with 're:' prefix (e.g., 're:^serena_.*')."
        ),
    )
    disabled_tools: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of tool names/patterns to disable after 'enabled_tools' filtering. "
            "Supports glob patterns and regex with 're:' prefix."
        ),
    )
    mcp_servers: Annotated[
        list[MCPServer],
        WithUnionMerge(merge_key="name"),
        AfterValidator(_unique_by("name")),
    ] = Field(
        default_factory=list, description="Preferred MCP server configuration entries."
    )
    enable_connectors: Annotated[bool, WithReplaceMerge()] = True
    enable_mcp: Annotated[bool, WithReplaceMerge()] = True
    experimental_enable_document_library_connector: Annotated[
        bool, WithReplaceMerge()
    ] = Field(
        default=False,
        description=(
            "Experimental: expose the Mistral document_library connector and its"
            " MCP tools. Off by default while the backend endpoints are"
            " stabilized; when enabled, document_library is requested from the"
            " connector bootstrap alongside web_search."
        ),
    )
    connectors: Annotated[list[ConnectorConfig], WithUnionMerge(merge_key="name")] = (
        Field(
            default_factory=list,
            description="Per-connector settings (disable, disabled_tools).",
        )
    )

    # Agents
    agent_paths: Annotated[list[Path], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for custom agent profiles. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of agent names/patterns to enable. If set, only these"
            " agents will be available. Supports glob patterns (e.g., 'custom-*')"
            " and regex with 're:' prefix."
        ),
    )
    disabled_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of agent names/patterns to disable. Ignored if 'enabled_agents'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    installed_agents: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of opt-in builtin agent names that have been explicitly installed."
        ),
    )
    default_agent: Annotated[str, WithReplaceMerge()] = Field(
        default=BuiltinAgentName.ACCEPT_EDITS,
        description=(
            "Agent profile to use when no --agent flag is passed. "
            "Builtin: ask, accept-edits, smart-approve, auto-approve. "
            "Applies in both interactive and programmatic (-p/--prompt) mode."
        ),
    )
    # Smart approve rollout, driven by GrowthBook (see experiments/active.py). The two
    # flags are independent, mirroring model routing (#49523): one exposes the mode in
    # the picker, the other makes it the default. Kept off by default so smart approve
    # ships dark until a cohort is opted in.
    smart_approve_available: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Expose the smart-approve mode in the mode picker/cycle. "
            "Set by the vibe_cli_smart_approve experiment; does not change the default."
        ),
    )
    smart_approve_default: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Make smart-approve the default mode (implies it is available). "
            "Set by the vibe_cli_smart_approve_default experiment."
        ),
    )
    enable_auto_approve: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description=(
            "Allow the auto-approve (YOLO) mode: the 'auto-approve' agent and the "
            "--auto-approve/--yolo flag. When false, no agent with the 'yolo' safety "
            "level is available and the flag and bypass_tool_permissions are ignored "
            "(per-tool permissions still apply). Intended for admin-managed "
            "(enterprise) config to forbid auto-approving tool calls."
        ),
    )

    # Skills
    skill_paths: Annotated[
        list[Path], WithConcatMerge(), BeforeValidator(_expand_paths)
    ] = Field(
        default_factory=list,
        description=(
            "Additional directories to search for skills. "
            "Each path may be absolute or relative to the current working directory."
        ),
    )
    enabled_skills: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "An explicit list of skill names/patterns to enable. If set, only these"
            " skills will be active. Supports glob patterns (e.g., 'search-*') and"
            " regex with 're:' prefix."
        ),
    )
    disabled_skills: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list,
        description=(
            "A list of skill names/patterns to disable. Ignored if 'enabled_skills'"
            " is set. Supports glob patterns and regex with 're:' prefix."
        ),
    )
    experimental_enable_registry_skills: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental: pull shared workspace skills from Mistral"
            " (api.mistral.ai) and make them available alongside local skills."
            " Requires a Mistral provider and API key. Local skills take"
            " precedence on name collision."
        ),
    )
    experimental_enable_model_catalog: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental: fetch the curated model catalog from the Mistral"
            " provider at session start and cache it per user on disk. Requires"
            " a Mistral provider and API key; any failure silently falls back"
            " to local config."
        ),
    )
    experimental_enable_local_projects: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental, in development: switch Vibe Desktop to local projects"
            " (local_projects.toml) instead of project links. Not used yet."
            " Read once when Vibe Desktop starts its app server."
        ),
    )
    experimental_enable_pr_monitoring: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental: detect pull requests and merge requests created during"
            " a session (gh pr create, glab mr create) and discover the ones that"
            " already exist for the session's repository at session load"
            " (gh pr list, glab mr list), persist them as session artifacts, and"
            " expose them via the artifacts/ RPC namespace. Requires gh/glab"
            " (system-installed or bundled)."
        ),
    )
    experimental_enable_pr_discovery: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental: discover open pull requests / merge requests for the"
            " session's repository at session load (gh pr list, glab mr list),"
            " persist them as session artifacts, and notify clients. Runs only"
            " when the session has no existing PR artifacts. Local only."
        ),
    )

    # Tracing
    enable_otel: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Export OpenTelemetry traces for agent, model, and tool operations. "
            "Requires enable_telemetry to be enabled."
        ),
    )
    otel_endpoint: Annotated[str, WithReplaceMerge()] = Field(
        default="",
        description=(
            "Base URL of a custom OTLP/HTTP collector. Vibe appends /v1/traces. "
            "When empty, Vibe uses the configured Mistral telemetry endpoint."
        ),
    )
    otel_redaction: Annotated[OtelRedactionMode, WithReplaceMerge()] = Field(
        default=OtelRedactionMode.DEFAULT,
        description=(
            "Client-side span attribute redaction: default redacts sensitive "
            "values, strict redacts sensitive attributes, and none disables "
            "redaction."
        ),
    )
    console_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_CONSOLE_BASE_URL

    # Top-level scalars
    theme: Annotated[str, WithReplaceMerge()] = DEFAULT_THEME
    applied_migrations: Annotated[list[str], WithConcatMerge()] = Field(
        default_factory=list
    )
    disable_welcome_banner_animation: Annotated[bool, WithReplaceMerge()] = False
    cursor_blink: Annotated[bool, WithReplaceMerge()] = Field(
        default=True, description="Blink the input cursor. Disable for a steady cursor."
    )
    show_greeting: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description="Show greeting at startup (Mistral providers only, once per 24h).",
    )
    autocopy_to_clipboard: Annotated[bool, WithReplaceMerge()] = True
    enable_mouse_capture: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description=(
            "Capture the mouse for scrolling, clicks and in-app selection. "
            "Disable to keep the terminal's native text selection."
        ),
    )
    file_watcher_for_autocomplete: Annotated[bool, WithReplaceMerge()] = True
    ask_confirmation_on_exit: Annotated[bool, WithReplaceMerge()] = True
    displayed_workdir: Annotated[str, WithReplaceMerge()] = Field(
        default="",
        deprecated="Ignored by Unified execution; remove this setting from your config.",
        description="Deprecated: accepted for older configs, with no effect on Unified execution.",
    )
    context_warnings: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        deprecated="Ignored by Unified execution; remove this setting from your config.",
        description="Deprecated: accepted for older configs, with no effect on Unified execution.",
    )
    voice_mode_enabled: Annotated[bool, WithReplaceMerge()] = False
    narrator_enabled: Annotated[bool, WithReplaceMerge()] = False
    show_thinking_nodes: Annotated[bool, WithReplaceMerge()] = True
    show_subagent_status_list: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description=(
            "Show the subagent status list and read-only transcript views in the "
            "interactive prompt."
        ),
    )
    worktree_limit: Annotated[int, WithReplaceMerge()] = Field(
        default=15,
        ge=0,
        le=100,
        description="Maximum number of recent managed worktrees kept locally.",
    )
    experimental_worktree_copy_on_write: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Experimental, macOS only: create managed worktrees of a trusted"
            " repository by cloning an idle template checkout with APFS"
            " copy-on-write, which uses less disk and is faster on large"
            " repositories. Keeps one template checkout per repository under"
            " ~/.vibe/worktrees/.templates, and sets extensions.worktreeConfig"
            " in the repository's .git/config. Falls back to a normal checkout."
        ),
    )
    bypass_tool_permissions: Annotated[bool, WithReplaceMerge()] = False
    raise_on_compaction_failure: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        deprecated="Ignored by Unified execution; remove this setting from your config.",
        description="Deprecated: accepted for older configs, with no effect on Unified execution.",
    )
    enable_telemetry: Annotated[bool, WithReplaceMerge()] = True
    system_prompt_id: Annotated[str, WithReplaceMerge()] = SystemPrompt.CLI
    managed_shell_tools_enabled: Annotated[bool, WithReplaceMerge()] = False
    enable_background_processes: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description=(
            "Offer the agent background processes (tools.process in "
            "run_typescript). Off, commands only run to completion in the "
            "foreground."
        ),
    )
    enable_subagents: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        description=(
            "Offer the agent subagents (tools.subagent in run_typescript). Off, "
            "the agent works alone."
        ),
    )
    max_subagents: Annotated[int, WithReplaceMerge()] = Field(
        default=32,
        ge=1,
        description=(
            "Maximum number of subagents that can be alive at once; a spawn "
            "beyond this fails until a subagent is stopped."
        ),
    )
    max_concurrent_subagents: Annotated[int, WithReplaceMerge()] = Field(
        default=32,
        ge=1,
        description=(
            "Maximum number of subagents running a turn at the same time; "
            "spawning or messaging beyond this fails until a subagent "
            "finishes its turn."
        ),
    )
    compaction_prompt_id: Annotated[str, WithReplaceMerge()] = UtilityPrompt.COMPACT
    title_prompt_id: Annotated[str, WithReplaceMerge()] = UtilityPrompt.SESSION_TITLE
    include_commit_signature: Annotated[bool, WithReplaceMerge()] = True
    include_model_info: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        deprecated="No longer used; remove this setting from your config.",
        description="Retired: accepted for older configs, with no effect.",
    )
    include_project_context: Annotated[bool, WithReplaceMerge()] = True
    include_prompt_detail: Annotated[bool, WithReplaceMerge()] = Field(
        default=True,
        deprecated="No longer used; remove this setting from your config.",
        description="Retired: accepted for older configs, with no effect.",
    )
    strict_config: Annotated[bool, WithReplaceMerge()] = Field(
        default=False,
        description=(
            "Refuse a config with settings Vibe does not use (unknown, retired, or"
            " without effect) instead of warning about them. For programmatic and"
            " headless runs; also VIBE_STRICT_CONFIG=1."
        ),
    )
    enable_update_checks: Annotated[bool, WithReplaceMerge()] = True
    enable_auto_update: Annotated[bool, WithReplaceMerge()] = True
    enable_notifications: Annotated[bool, WithReplaceMerge()] = True
    experimental_enable_tab_status: Annotated[bool, WithReplaceMerge()] = True
    enable_system_trust_store: Annotated[bool, WithReplaceMerge()] = False
    api_timeout: Annotated[float, WithReplaceMerge()] = DEFAULT_API_TIMEOUT
    api_retry_max_elapsed_time: Annotated[float, WithReplaceMerge()] = (
        DEFAULT_API_RETRY_MAX_ELAPSED_TIME
    )
    api_stream_idle_timeout: Annotated[float, WithReplaceMerge()] = Field(
        default=DEFAULT_API_STREAM_IDLE_TIMEOUT,
        ge=0,
        description=(
            "Seconds of silence after the model starts answering before the "
            "response counts as stalled and is retried once. 0 allows the same "
            "silence as before the first output (api_timeout). Unified harness only."
        ),
    )
    agent_max_output_tokens: Annotated[int, WithReplaceMerge()] = Field(
        default=0,
        ge=0,
        description=(
            "Output token cap sent as max_tokens on every agent and subagent "
            "request, whatever the provider, held to the model's max output and "
            "to the room the context window leaves above the compaction "
            "threshold. 0 sends a cap only where the provider requires one: the "
            "model's max_output_tokens. Unified harness only."
        ),
    )
    compaction_max_output_tokens: Annotated[int, WithReplaceMerge()] = Field(
        default=0,
        ge=0,
        description=(
            "Output token cap sent as max_tokens on every compaction request, "
            "whatever the provider, held to the compaction model's max output and "
            "to the room its context window leaves above the compaction "
            "threshold. 0 sends a cap only where the provider requires one: the "
            "compaction model's max_output_tokens. Unified harness only."
        ),
    )
    compaction_reasoning_limit_tokens: Annotated[int, WithReplaceMerge()] = Field(
        default=DEFAULT_COMPACTION_REASONING_LIMIT_TOKENS,
        ge=0,
        description=(
            "Approximate reasoning tokens after which the first compaction "
            "request is stopped, as if cut off at its output limit, and the "
            "summary is asked for once more, briefly, without tools and with "
            "reasoning off or at its lowest level. Reasoning is no longer "
            "watched once the answer starts. 0 disables the limit. Unified "
            "harness only."
        ),
    )
    output_limit_retries: Annotated[int, WithReplaceMerge()] = Field(
        default=DEFAULT_OUTPUT_LIMIT_RETRIES,
        ge=0,
        description=(
            "Answers cut off at the output token limit that a turn discards and "
            "asks again for, with feedback asking for a shorter response. 0 ends "
            "the turn on the first cut-off answer. Unified harness only."
        ),
    )
    api_connect_timeout: Annotated[float, WithReplaceMerge()] = Field(
        default=10.0,
        deprecated="No longer used; remove this setting from your config.",
        description="Retired: accepted for older configs, with no effect.",
    )
    api_write_timeout: Annotated[float, WithReplaceMerge()] = Field(
        default=30.0,
        deprecated="No longer used; remove this setting from your config.",
        description="Retired: accepted for older configs, with no effect.",
    )
    api_pool_timeout: Annotated[float, WithReplaceMerge()] = Field(
        default=10.0,
        deprecated="No longer used; remove this setting from your config.",
        description="Retired: accepted for older configs, with no effect.",
    )
    vibe_base_url: Annotated[str, WithReplaceMerge()] = DEFAULT_VIBE_BASE_URL
    vibe_code_sessions_base_url: Annotated[str, WithReplaceMerge()] = (
        "https://chat.mistral.ai"
    )
    log_level: Annotated[
        str | None, WithReplaceMerge(), BeforeValidator(_normalize_log_level)
    ] = None

    # Nested configs: a bag of independent settings, so a layer overrides only
    # the keys it names. Shallow rather than deep on purpose -- these are flat
    # scalars today, and deep merging would silently extend per-key merging to
    # the first dict-valued setting anyone adds, where a user could then shadow
    # an inherited key but never remove it.
    project_context: Annotated[ProjectContextConfig, WithShallowMerge()] = Field(
        default_factory=ProjectContextConfig
    )
    session_logging: Annotated[SessionLoggingConfig, WithShallowMerge()] = Field(
        default_factory=SessionLoggingConfig
    )
    experiments: Annotated[ExperimentsConfig, WithShallowMerge()] = Field(
        default_factory=ExperimentsConfig
    )

    def smart_approve_offered(self) -> bool:
        """Whether smart-approve is exposed in the mode picker/cycle.

        Making it the default implies it is offered, so a cohort can be defaulted
        into it without also flipping the availability flag.
        """
        return self.smart_approve_available or self.smart_approve_default

    def resolve_default_model_alias(self) -> str:
        available = self.available_models()
        if self.routed_default_model and self.routed_default_model in available:
            return self.routed_default_model
        if self.catalog_recommended_model in available:
            return self.catalog_recommended_model
        if DEFAULT_ACTIVE_MODEL_CONFIG.alias in available or not available:
            # If there are no available models, fallback to default
            return DEFAULT_ACTIVE_MODEL_CONFIG.alias
        return next(iter(available))

    def available_models(self) -> dict[str, ModelConfig]:
        if not self.allowed_models:
            return self.models
        allowed = {
            alias: model
            for alias, model in self.models.items()
            if name_matches(model.name, self.allowed_models)
        }
        if self.origin_of("allowed_models") == "admin":
            # An administrator's policy must fail closed: an invalid policy must
            # not allow a user-configured model to run.
            return allowed
        # A filter that matches nothing in a non-enforced config degrades to
        # "allow all" rather than bricking model selection; the mismatch already
        # surfaces as a warning.
        return allowed or self.models

    def get_active_model(self) -> ModelConfig:
        if self.active_model and self.active_model not in self.models:
            raise ValueError(
                f"Active model '{self.active_model}' not found in configuration."
            )
        # An allowlist-excluded pin never runs: fall back to the default allowed model.
        available = self.available_models()
        alias = (
            self.active_model
            if self.active_model in available
            else self.resolve_default_model_alias()
        )
        if model := available.get(alias):
            return model
        raise ValueError(
            f"Active model '{self.active_model}' not found in configuration."
        )

    def get_provider_for_model(self, model: ModelConfig) -> ProviderConfig:
        if provider := next(
            (p for p in self.providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"Provider '{model.provider}' for model '{model.name}' not found in configuration."
        )

    def get_compaction_model(self) -> ModelConfig:
        if self.compaction_model is not None and self._shares_active_provider(
            self.compaction_model
        ):
            return self.compaction_model
        return self.get_active_model()

    def _shares_active_provider(self, model: ModelConfig) -> bool:
        # Compaction rides the active model's connection, so a model on another
        # provider would be sent to the wrong endpoint.
        try:
            active_provider = self.get_provider_for_model(self.get_active_model())
        except ValueError:
            return True
        return model.provider == active_provider.name

    def get_vision_fallback_model(self) -> ModelConfig | None:
        try:
            active = self.get_active_model()
        except ValueError:
            return self.vision_model
        # Describing an image the model is about to receive anyway only loses
        # detail.
        if active.supports_images:
            return None
        if self.vision_model is not None:
            return self.vision_model
        # Same provider means same key and same endpoint, so a blind model
        # picks up vision with no config and no image leaves where the session
        # was already talking. Crossing providers stays the user's call.
        return next(
            (
                model
                for model in self.available_models().values()
                if model.supports_images and model.provider == active.provider
            ),
            None,
        )

    def get_utility_model(self, feature: UtilityFeature) -> ModelConfig | None:
        """The model configured for ``feature``; None means automatic selection.

        A model aliased ``active`` wins over the active-model selector.
        """
        alias = self.utility_models.alias_for(feature)
        if not alias:
            return None
        available = self.available_models()
        if alias in available:
            return available[alias]
        if alias == ACTIVE_MODEL_SELECTOR and alias not in self.models:
            return self.get_active_model()
        return None

    def connectors_by_name(self) -> dict[str, ConnectorConfig]:
        return {c.name: c for c in self.connectors}

    def get_active_provider(self) -> ProviderConfig:
        return self.get_provider_for_model(self.get_active_model())

    def require_active_provider_api_key(self) -> None:
        try:
            provider = self.get_active_provider()
        except ValueError:
            return
        api_key_env = provider.api_key_env_var
        if api_key_env and not resolve_api_key(api_key_env):
            raise MissingAPIKeyError(
                api_key_env,
                provider.name,
                keyring_error=keyring_read_failure(api_key_env),
            )

    def get_mistral_provider(self) -> ProviderConfig | None:
        try:
            active_provider = self.get_active_provider()
            if active_provider.backend == Backend.MISTRAL:
                return active_provider
        except ValueError:
            pass
        return next((p for p in self.providers if p.backend == Backend.MISTRAL), None)

    def resolve_mistral_api_key(self) -> str:
        provider = self.get_mistral_provider()
        if provider is None:
            return ""
        return resolve_api_key(provider.api_key_env_var) or ""

    def is_active_model_mistral(self) -> bool:
        try:
            return self.get_active_provider().backend == Backend.MISTRAL
        except ValueError:
            return False

    def get_active_transcribe_model(self) -> TranscribeModelConfig:
        if model := next(
            (
                m
                for m in self.transcribe_models
                if m.alias == self.active_transcribe_model
            ),
            None,
        ):
            return model
        raise ValueError(
            f"Active transcribe model '{self.active_transcribe_model}' not found in configuration."
        )

    def get_transcribe_provider_for_model(
        self, model: TranscribeModelConfig
    ) -> TranscribeProviderConfig:
        if provider := next(
            (p for p in self.transcribe_providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"Transcribe provider '{model.provider}' for transcribe model '{model.name}' not found in configuration."
        )

    def get_active_tts_model(self) -> TTSModelConfig:
        if model := next(
            (m for m in self.tts_models if m.alias == self.active_tts_model), None
        ):
            return model
        raise ValueError(
            f"Active TTS model '{self.active_tts_model}' not found in configuration."
        )

    def get_tts_provider_for_model(self, model: TTSModelConfig) -> TTSProviderConfig:
        if provider := next(
            (p for p in self.tts_providers if p.name == model.provider), None
        ):
            return provider
        raise ValueError(
            f"TTS provider '{model.provider}' for TTS model '{model.name}' not found in configuration."
        )

    def build_tool_allowlist_update(
        self,
        tool_name: str,
        patterns: list[str],
        *,
        current_allowlist: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Extend a tool's allowlist in memory and return the persist payload.

        Returns ``None`` when every pattern is already allowlisted. Callers
        persist the returned payload; the in-memory config is kept current so
        repeated calls merge from fresh state.
        """
        if tool_name in _SESSION_PATTERN_WILDCARD_TOOLS:
            patterns = [_strip_session_pattern_wildcard(p) for p in patterns]
        allowlist: list[str] = list(
            current_allowlist
            if current_allowlist is not None
            else self.tools.get(tool_name, {}).get("allowlist", [])
        )
        new_patterns = [p for p in patterns if p not in allowlist]
        if not new_patterns:
            return None
        merged = sorted(allowlist + new_patterns)
        self.tools.setdefault(tool_name, {})["allowlist"] = merged
        return {"tools": {tool_name: {"allowlist": merged}}}

    @property
    def system_prompt(self) -> str:
        return load_system_prompt(
            self.system_prompt_id, harness_files=self._harness_files
        )

    @property
    def compaction_prompt(self) -> str:
        return load_prompt(
            self.compaction_prompt_id,
            setting_name="compaction_prompt_id",
            builtins={"compact": UtilityPrompt.COMPACT.path},
            harness_files=self._harness_files,
        )

    @property
    def title_prompt(self) -> str:
        return load_prompt(
            self.title_prompt_id,
            setting_name="title_prompt_id",
            builtins={"session_title": UtilityPrompt.SESSION_TITLE.path},
            harness_files=self._harness_files,
        )

    @model_validator(mode="after")
    def _inject_routed_model(self) -> VibeConfigSchema:
        alias = self.routed_default_model
        model = self.routed_model_config
        if not (alias and model is not None and model.alias == alias):
            return self

        existing = self.models.get(alias)
        if existing is None:
            object.__setattr__(self, "models", {**self.models, alias: model})
        else:
            user_overrides = {
                field: getattr(existing, field) for field in existing.model_fields_set
            }
            object.__setattr__(
                self,
                "models",
                {**self.models, alias: model.model_copy(update=user_overrides)},
            )
        return self

    @model_validator(mode="after")
    def _inject_routed_extra_models(self) -> VibeConfigSchema:
        # Adds models to the dropdown without touching ``routed_default_model``,
        # so exposure/rollout never changes the resolved default. Composes with
        # ``_inject_routed_model``: if ``routed_default_model`` names an alias
        # added here, ``resolve_default_model_alias`` still promotes it to default.
        if not self.routed_extra_models:
            return self
        models = dict(self.models)
        for model in self.routed_extra_models:
            # An empty alias collides with the unpinned ``active_model`` sentinel
            # (""), so a malformed payload could hijack the resolved default. Skip
            # it: injection must never touch the default.
            if not model.alias:
                continue
            existing = models.get(model.alias)
            if existing is None:
                models[model.alias] = model
                continue
            # A user-defined entry for the same alias always wins over injection.
            user_overrides = {
                field: getattr(existing, field) for field in existing.model_fields_set
            }
            models[model.alias] = model.model_copy(update=user_overrides)
        object.__setattr__(self, "models", models)
        return self

    _refuse_zero_threshold = field_validator("auto_compact_threshold", mode="before")(
        refuse_zero_auto_compact_threshold
    )

    @model_validator(mode="after")
    def _apply_auto_compact_threshold(self) -> VibeConfigSchema:
        # A model without its own threshold takes the global one. With neither,
        # the threshold stays unset and the session derives it from the model's
        # context window; a threshold outside its window's range fails there.
        threshold = self.auto_compact_threshold
        if threshold is None:
            return self
        models = {
            alias: model
            if model.auto_compact_threshold is not None
            else model.model_copy(update={"auto_compact_threshold": threshold})
            for alias, model in self.models.items()
        }
        object.__setattr__(self, "models", models)
        return self

    @model_validator(mode="after")
    def _resolve_model_aliases(self) -> VibeConfigSchema:
        index = build_alias_index(
            {
                model_id: aliases
                for model_id, aliases in self.catalog_model_aliases.items()
                if model_id in self.models
            },
            set(self.models),
        )
        for alias, reason in index.dropped.items():
            warn_ignored_alias(alias, reason)
        if target := index.targets.get(self.active_model):
            object.__setattr__(self, "active_model", target)
        return self

    @model_validator(mode="after")
    def _apply_active_model_fallback(self) -> VibeConfigSchema:
        # The empty string is the "unpinned/default" sentinel: it is always valid
        # and resolves to a configured model at read time (get_active_model).
        if self.active_model and self.active_model not in self.models:
            unknown = self.active_model
            fallback = self.resolve_default_model_alias()
            fallback_model = self.models.get(fallback)
            label = fallback_model.display_name if fallback_model else None
            # States what was observed, never a cause: a retired catalog model,
            # an unreachable catalog and a typo all look the same from here.
            message = (
                f"Model '{unknown}' is not available — running '{label or fallback}'. "
                "Pick another model to stop this notice."
            )
            logger.warning("%s", message)
            self._validation_warnings.append(message)
            object.__setattr__(self, "_missing_pin", unknown)
            object.__setattr__(self, "active_model", UNPINNED_ACTIVE_MODEL)
        return self

    @model_validator(mode="after")
    def _warn_unlisted_thinking(self) -> VibeConfigSchema:
        # The provider still judges the word; the warning flags a likely typo,
        # and the pickers offer the level beside the model's own to move off it.
        for model in self.models.values():
            if not model.thinking_is_set:
                continue
            # Like the harness: a standard word in any case, a model's own as written.
            written = model.thinking.strip()
            if written.lower() in THINKING_LEVELS or written in model.thinking_levels:
                continue
            if self._mistral_sdk_drops_thinking(model):
                continue
            message = (
                f"Model '{model.alias}': thinking {model.thinking!r} is neither a "
                f"standard level ({', '.join(THINKING_LEVELS)}) nor in its "
                f"thinking_levels ({', '.join(model.thinking_levels)}); the "
                "provider may refuse it. Pick another thinking level."
            )
            logger.warning("%s", message)
            self._validation_warnings.append(message)
        return self

    def _mistral_sdk_drops_thinking(self, model: ModelConfig) -> bool:
        if not model.thinking_is_set:
            return False
        written = model.thinking.strip()
        if written.lower() in THINKING_LEVELS or written in _mistral_efforts():
            return False
        return any(
            provider.name == model.provider and provider.backend == Backend.MISTRAL
            for provider in self.providers
        )

    @model_validator(mode="after")
    def _warn_thinking_the_mistral_sdk_drops(self) -> VibeConfigSchema:
        for model in self.models.values():
            if not self._mistral_sdk_drops_thinking(model):
                continue
            message = (
                f"Model '{model.alias}': the Mistral client cannot send thinking "
                f"{model.thinking!r}, so its requests go without a thinking level "
                f"(it sends {', '.join(_mistral_thinking_levels())}). Pick another "
                "thinking level."
            )
            logger.warning("%s", message)
            self._validation_warnings.append(message)
        return self

    @model_validator(mode="after")
    def _warn_unmatched_allowed_models(self) -> VibeConfigSchema:
        for pattern in self.allowed_models:
            if not (pattern or "").strip():
                continue
            if any(
                name_matches(model.name, [pattern]) for model in self.models.values()
            ):
                continue
            logger.warning(
                "Allowed model '%s' matches none of your configured models.", pattern
            )
            self._validation_warnings.append(
                f"Allowed model '{pattern}' matches none of your configured models."
            )
        return self

    @model_validator(mode="after")
    def _warn_disallowed_active_model(self) -> VibeConfigSchema:
        if not self.active_model or self.active_model in self.available_models():
            return self
        fallback = self.resolve_default_model_alias()
        logger.warning(
            "Active model '%s' is excluded by allowed_models; "
            "falling back to default model '%s'.",
            self.active_model,
            fallback,
        )
        self._validation_warnings.append(
            f"Active model '{self.active_model}' is excluded by allowed_models "
            f"— falling back to default model '{fallback}'."
        )
        return self

    @model_validator(mode="after")
    def _warn_removed_default_agent(self) -> VibeConfigSchema:
        if (replacement := REMOVED_BUILTIN_AGENTS.get(self.default_agent)) is None:
            return self
        self._validation_warnings.append(
            f"Default agent '{self.default_agent}' is no longer built in — sessions "
            f"start on '{replacement}' unless a custom agent is named "
            f"'{self.default_agent}'. Set default_agent to another agent."
        )
        return self

    @model_validator(mode="after")
    def _warn_unknown_utility_models(self) -> VibeConfigSchema:
        # Mirrors get_utility_model without resolving the active model, which
        # can raise under a fail-closed allowlist and would fail config loading.
        available = self.available_models()
        for feature, alias in self.utility_models.configured():
            if alias in available:
                continue
            if alias == ACTIVE_MODEL_SELECTOR and alias not in self.models:
                continue
            reason = (
                "is excluded by allowed_models"
                if alias in self.models
                else "is not one of your configured models"
            )
            logger.warning(
                "Utility model '%s' for '%s' %s; "
                "that feature falls back to automatic model selection.",
                alias,
                feature.value,
                reason,
            )
            self._validation_warnings.append(
                f"Utility model '{alias}' for '{feature.value}' {reason} — that "
                "feature falls back to automatic model selection."
            )
        return self

    @model_validator(mode="after")
    def _warn_cross_provider_compaction_model(self) -> VibeConfigSchema:
        if self.compaction_model is None:
            return self

        compaction_provider = self.get_provider_for_model(self.compaction_model)
        if self._shares_active_provider(self.compaction_model):
            return self
        active_model = self.get_active_model()
        logger.warning(
            "Compaction model '%s' uses provider '%s' but active model '%s' uses "
            "provider '%s'; compacting with the active model instead.",
            self.compaction_model.alias,
            compaction_provider.name,
            active_model.alias,
            active_model.provider,
        )
        self._validation_warnings.append(
            f"Compaction model '{self.compaction_model.alias}' uses provider "
            f"'{compaction_provider.name}', not the active model's "
            f"'{active_model.provider}' — compacting with '{active_model.alias}' "
            "instead."
        )
        return self

    @model_validator(mode="after")
    def _check_vision_model(self) -> VibeConfigSchema:
        if self.vision_model is None:
            return self
        if not self.vision_model.supports_images:
            raise ValueError(
                f"Vision model '{self.vision_model.alias}' must set "
                "supports_images = true."
            )
        # Deliberately no same-provider check, unlike `compaction_model`:
        # crossing providers is the whole point of setting this, and the
        # description is a standalone completion on its own backend.
        self.get_provider_for_model(self.vision_model)
        return self

    @model_validator(mode="after")
    def _check_system_prompt(self, info: ValidationInfo) -> VibeConfigSchema:
        _ = load_system_prompt(
            self.system_prompt_id,
            harness_files=_harness_files_from_context(info.context),
        )
        return self

    @model_validator(mode="after")
    def _check_compaction_prompt(self, info: ValidationInfo) -> VibeConfigSchema:
        _ = load_prompt(
            self.compaction_prompt_id,
            setting_name="compaction_prompt_id",
            builtins={"compact": UtilityPrompt.COMPACT.path},
            harness_files=_harness_files_from_context(info.context),
        )
        return self

    @model_validator(mode="after")
    def _check_title_prompt(self, info: ValidationInfo) -> VibeConfigSchema:
        _ = load_prompt(
            self.title_prompt_id,
            setting_name="title_prompt_id",
            builtins={"session_title": UtilityPrompt.SESSION_TITLE.path},
            harness_files=_harness_files_from_context(info.context),
        )
        return self


def outside_workdir_denied(config: VibeConfigSchema) -> bool:
    """Whether outside-workdir access is refused globally or for any tool."""
    return config.outside_workdir is OutsideWorkdirPolicy.DENY or any(
        tool.get("outside_workdir") == OutsideWorkdirPolicy.DENY
        for tool in config.tools.values()
    )


def create_default_config() -> dict[str, Any]:
    from vibe.core.tools.manager import discover_tool_defaults

    config_dict = VibeConfigSchema.model_construct().model_dump(
        mode="json", exclude_none=True
    )
    if isinstance(config_dict.get("models"), dict):
        config_dict["models"] = serialize_model_configs(config_dict["models"])
    if tool_defaults := discover_tool_defaults():
        config_dict["tools"] = tool_defaults
    return config_dict
