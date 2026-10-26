from __future__ import annotations

from collections.abc import Iterable, Sequence
import hashlib
from typing import Literal

from vibe.app_server.config import (
    AudioProviderView,
    ConfigView,
    ModelConfigView,
    ModelSource,
    SpeechConfigView,
    TranscribeModelConfigView,
    TranscriptionConfigView,
    TTSModelConfigView,
)
from vibe.app_server.models import (
    AgentSummary,
    DebugLogEntry,
    DebugLogPage,
    SkillSummary,
)
from vibe.config_values import ThinkingLevel
from vibe.core.agents import AgentProfile
from vibe.core.config import (
    ModelConfig,
    TranscribeClient,
    TranscribeModelConfig,
    TranscribeProviderConfig,
    TTSClient,
    TTSModelConfig,
    TTSProviderConfig,
    VibeConfigSchema,
)
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.layers.agent_profile import AgentProfileLayer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.environment import EnvironmentLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.layers.model_catalog import ModelCatalogLayer
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.config.layers.user import SuppliedUserConfigLayer, UserConfigLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.log_reader import PaginatedLogs
from vibe.core.skills.models import SkillInfo, SkillSource
from vibe.core.utils import name_matches

# Model picker display priority, each layer with the group it lists models
# under: org-curated sources first, the user's own last. Display only -- which
# layer's values win is the merge's call.
_MODEL_SOURCES: tuple[tuple[str, ModelSource], ...] = (
    (AdminConfigLayer.NAME, "admin"),
    (GrowthbookLayer.NAME, "growthbook"),
    (ModelCatalogLayer.NAME, "catalog"),
    (ProjectConfigLayer.NAME, "project"),
    (AgentProfileLayer.NAME, "project"),
    (EnvironmentLayer.NAME, "project"),
    (OverridesLayer.NAME, "project"),
    (UserConfigLayer.NAME, "local"),
    # Stands in for the user file when an embedding host supplies the config.
    (SuppliedUserConfigLayer.NAME, "local"),
    (DefaultConfigLayer.NAME, "local"),
)


def project_config_view(
    config: VibeConfigSchema,
    *,
    active_model_pinned: bool = False,
    awaiting_experiment_model: bool = False,
    # Whether the caller's backend can show a blind model a description in
    # place of the pixels. Only the Unified one can, so the legacy projection
    # keeps reporting the active model's own vision.
    image_fallback: bool = False,
) -> ConfigView:
    transcribe_model = config.get_active_transcribe_model()
    tts_model = config.get_active_tts_model()
    active_model = config.get_active_model()
    # Only a describer makes the promise good: without one a blind model gets
    # ``[Image]`` in place of the pixels, so the send is refused instead.
    describable = image_fallback and config.get_vision_fallback_model() is not None
    sources = _model_sources(config)
    available = config.available_models()
    # Grouped by source; a model no known layer defines keeps its place, last.
    models = [available[alias] for alias in sources if alias in available]
    models += [model for alias, model in available.items() if alias not in sources]
    return ConfigView(
        active_model=_project_model_config(
            active_model,
            image_fallback=image_fallback,
            source=sources.get(active_model.alias),
            route_facts=_route_facts(config, active_model),
        ),
        active_model_pinned=active_model_pinned,
        images_supported=active_model.supports_images or describable,
        awaiting_experiment_model=awaiting_experiment_model,
        # The configured default, never the active model: clients render it as
        # the "Default (currently X)" hint, which must stay stable while a pin
        # is in effect.
        default_model_alias=config.resolve_default_model_alias(),
        default_agent=config.default_agent,
        theme=config.theme,
        log_level=config.log_level,
        disable_welcome_banner_animation=config.disable_welcome_banner_animation,
        cursor_blink=config.cursor_blink,
        show_greeting=config.show_greeting,
        autocopy_to_clipboard=config.autocopy_to_clipboard,
        enable_mouse_capture=config.enable_mouse_capture,
        file_watcher_for_autocomplete=config.file_watcher_for_autocomplete,
        ask_confirmation_on_exit=config.ask_confirmation_on_exit,
        voice_mode_enabled=config.voice_mode_enabled,
        narrator_enabled=config.narrator_enabled,
        show_thinking_nodes=config.show_thinking_nodes,
        show_subagent_status_list=config.show_subagent_status_list,
        worktree_limit=config.worktree_limit,
        enable_update_checks=config.enable_update_checks,
        enable_notifications=config.enable_notifications,
        enable_system_trust_store=config.enable_system_trust_store,
        experimental_enable_tab_status=config.experimental_enable_tab_status,
        enable_telemetry=config.enable_telemetry,
        experimental_enable_registry_skills=config.experimental_enable_registry_skills,
        models=[
            _project_model_config(
                model,
                image_fallback=image_fallback,
                source=sources.get(model.alias),
                route_facts=_route_facts(config, model),
            )
            for model in models
        ],
        transcribe_models=[model.alias for model in config.transcribe_models],
        tts_models=[model.alias for model in config.tts_models],
        transcription=TranscriptionConfigView(
            model=_project_transcribe_model(transcribe_model),
            provider=_project_transcribe_provider(
                config.get_transcribe_provider_for_model(transcribe_model)
            ),
        ),
        speech=SpeechConfigView(
            model=_project_tts_model(tts_model),
            provider=_project_tts_provider(
                config.get_tts_provider_for_model(tts_model)
            ),
        ),
        validation_warnings=list(config.validation_warnings),
    )


def _project_audio_client(client: TranscribeClient | TTSClient) -> Literal["mistral"]:
    match client:
        case TranscribeClient.MISTRAL | TTSClient.MISTRAL:
            return "mistral"
    raise ValueError(f"Unsupported audio client: {client}")


def _model_sources(config: VibeConfigSchema) -> dict[str, ModelSource]:
    """Each model alias's group, from the highest-priority layer defining it.

    The keys come in picker order: walking the layers by priority, an alias
    first appears under its highest defining layer, in that layer's own order
    (the catalog's order for the catalog group). A layer that only adjusts a
    model, such as a thinking level a pick writes for the session, defines
    nothing and never moves it.
    """
    defined = config.defined_entries_by_layer("models")
    sources: dict[str, ModelSource] = {}
    for layer, source in _MODEL_SOURCES:
        for alias in defined.get(layer, ()):
            sources.setdefault(alias, source)
    return sources


def offered_levels(config: VibeConfigSchema, model: ModelConfig) -> list[ThinkingLevel]:
    return _route_facts(config, model)[2]


def _route_facts(
    config: VibeConfigSchema, model: ModelConfig
) -> tuple[int | None, ThinkingLevel, list[ThinkingLevel]]:
    """Where ``model`` compacts, unset thresholds included, the thinking level
    it runs, and the levels a picker offers.

    Clients show context usage against the threshold, so an unset one reports
    the model's default rather than nothing. The level shown is the one the
    session runs, always among the offered ones. A model whose settings no
    profile can hold reports its configured values: opening a session with it
    says why.
    """
    from vibe.app_server._completion_facts import (
        ModelFactsError,
        compile_agent_route_facts,
    )

    try:
        facts = compile_agent_route_facts(config.get_provider_for_model(model), model)
    except (ModelFactsError, ValueError):
        levels = list(model.thinking_levels)
        if model.thinking not in levels:
            levels.append(model.thinking)
        return model.auto_compact_threshold, model.thinking, levels
    return facts.compaction_threshold, facts.thinking, list(facts.thinking_levels)


def _project_model_config(
    model: ModelConfig,
    *,
    image_fallback: bool,
    source: ModelSource | None,
    route_facts: tuple[int | None, ThinkingLevel, list[ThinkingLevel]],
) -> ModelConfigView:
    compaction_threshold, thinking, thinking_levels = route_facts
    image_delivery: Literal["native", "resource_link"] | None = None
    if model.supports_images:
        image_delivery = "native"
    elif image_fallback:
        image_delivery = "resource_link"
    return ModelConfigView(
        name=model.name,
        alias=model.alias,
        thinking=thinking,
        supports_images=model.supports_images is True,
        display_name=model.display_name or model.alias,
        max_context_length=model.max_context_length,
        auto_compact_threshold=compaction_threshold,
        image_delivery=image_delivery,
        thinking_levels=thinking_levels,
        source=source,
    )


def _project_transcribe_model(
    model: TranscribeModelConfig,
) -> TranscribeModelConfigView:
    return TranscribeModelConfigView(
        name=model.name,
        sample_rate=model.sample_rate,
        encoding=model.encoding,
        language=model.language,
        target_streaming_delay_ms=model.target_streaming_delay_ms,
    )


def _project_transcribe_provider(
    provider: TranscribeProviderConfig,
) -> AudioProviderView:
    return AudioProviderView(
        api_base=provider.api_base,
        api_key_env_var=provider.api_key_env_var,
        client=_project_audio_client(provider.client),
    )


def _project_tts_model(model: TTSModelConfig) -> TTSModelConfigView:
    return TTSModelConfigView(
        name=model.name, voice=model.voice, response_format=model.response_format
    )


def _project_tts_provider(provider: TTSProviderConfig) -> AudioProviderView:
    return AudioProviderView(
        api_base=provider.api_base,
        api_key_env_var=provider.api_key_env_var,
        client=_project_audio_client(provider.client),
    )


def project_agent_summaries(
    active: AgentProfile, available: Iterable[AgentProfile]
) -> tuple[AgentSummary, list[AgentSummary]]:
    return project_agent_summary(active), [
        project_agent_summary(profile) for profile in available
    ]


def project_skill_summaries(skills: Iterable[SkillInfo]) -> list[SkillSummary]:
    return [_skill_summary(skill) for skill in skills]


def _skill_summary(
    skill: SkillInfo, enabled: bool = True, locked: bool = False
) -> SkillSummary:
    return SkillSummary.model_validate({
        "name": skill.name,
        "description": skill.description,
        "prompt": skill.prompt,
        "user_invocable": skill.user_invocable,
        "source": skill.source.value,
        "scope": skill.scope.value,
        "registry": skill.registry.model_dump() if skill.registry else None,
        "enabled": enabled,
        "locked": locked,
    })


def writable_disabled_skills(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> Sequence[str] | None:
    """The writable layer's own ``disabled_skills``, or None when unavailable.

    ``disabled_skills`` concat-merges, so the effective list is every layer at
    once while a toggle can only add to or remove from the writable one. Reads
    the cached layer rather than loading, to stay usable from sync projection.
    """
    try:
        layer = orchestrator.get_layer(orchestrator.writable_layer_name)
    except KeyError:
        return None
    data = layer.cached_data
    if data is None:
        return None
    names = getattr(data, "disabled_skills", None)
    return list(names) if names else []


def project_installed_skill_summaries(
    skills: Iterable[SkillInfo],
    config: VibeConfigSchema,
    user_disabled: Sequence[str] | None = None,
) -> list[SkillSummary]:
    """Browser rows for *skills*, marked against the config's skill filters.

    ``enabled`` is whether the agent loads the skill. ``locked`` is whether the
    browser can change that, and the two are independent: a skill can be
    enabled and locked, or disabled and locked.

    ``user_disabled`` is the writable layer's own ``disabled_skills``.
    ``disabled_skills`` concatenates across layers, so a name a project or admin
    layer disabled cannot be re-enabled by rewriting the writable one; without
    it a row offers a toggle that silently does nothing. Omitting the argument
    assumes a single layer.

    Plugin skills are locked: they come from an installed plugin and are managed
    through ``/plugins``, not by this config.
    """
    allowed = config.enabled_skills
    disabled = config.disabled_skills
    own = list(disabled if user_disabled is None else user_disabled)

    def _enabled(info: SkillInfo) -> bool:
        if info.source is SkillSource.PLUGIN:
            return True
        if allowed:
            return name_matches(info.name, allowed)
        return not (disabled and name_matches(info.name, disabled))

    def _locked(info: SkillInfo) -> bool:
        if info.source is SkillSource.PLUGIN:
            return True
        if allowed:
            return True
        if not disabled:
            return False
        patterns = [pattern for pattern in disabled if pattern != info.name]
        if name_matches(info.name, patterns):
            return True
        return name_matches(info.name, disabled) and info.name not in own

    return [_skill_summary(info, _enabled(info), _locked(info)) for info in skills]


def project_debug_logs(logs: PaginatedLogs) -> DebugLogPage:
    return DebugLogPage(
        entries=[
            DebugLogEntry(
                id=hashlib.sha1(
                    entry.raw_line.encode("utf-8"), usedforsecurity=False
                ).hexdigest(),
                timestamp=entry.timestamp,
                ppid=entry.ppid,
                pid=entry.pid,
                level=entry.level,
                message=entry.message,
                raw_line=entry.raw_line,
            )
            for entry in logs.entries
        ],
        has_more=logs.has_more,
        cursor=logs.cursor,
    )


def project_agent_summary(profile: AgentProfile) -> AgentSummary:
    return AgentSummary(
        name=profile.name,
        display_name=profile.display_name,
        description=profile.description,
        safety=profile.safety,
        agent_type=profile.agent_type,
    )
