from __future__ import annotations

from vibe.app_server._model import ProtocolModel
from vibe.config_values import (
    THINKING_LEVELS as THINKING_LEVELS,
    AudioClient,
    SpeechOutputFormat,
    ThinkingLevel as ThinkingLevel,
    TranscriptionEncoding,
)


class ModelConfigView(ProtocolModel):
    name: str
    alias: str
    thinking: ThinkingLevel
    supports_images: bool
    display_name: str


class TranscribeModelConfigView(ProtocolModel):
    name: str
    sample_rate: int
    encoding: TranscriptionEncoding
    language: str
    target_streaming_delay_ms: int


class AudioProviderView(ProtocolModel):
    api_base: str
    api_key_env_var: str
    client: AudioClient


class TranscriptionConfigView(ProtocolModel):
    model: TranscribeModelConfigView
    provider: AudioProviderView


class TTSModelConfigView(ProtocolModel):
    name: str
    voice: str
    response_format: SpeechOutputFormat


class SpeechConfigView(ProtocolModel):
    model: TTSModelConfigView
    provider: AudioProviderView


class ProxySettingsView(ProtocolModel):
    values: dict[str, str | None]
    descriptions: dict[str, str]


class ConfigView(ProtocolModel):
    active_model: ModelConfigView
    # Whether the user has pinned a specific model, vs. the "default" (unpinned)
    active_model_pinned: bool
    # Cold-cache first launch
    awaiting_experiment_model: bool = False
    default_model_alias: str
    theme: str
    log_level: str | None
    disable_welcome_banner_animation: bool
    show_greeting: bool
    autocopy_to_clipboard: bool
    file_watcher_for_autocomplete: bool
    ask_confirmation_on_exit: bool
    voice_mode_enabled: bool
    narrator_enabled: bool
    show_thinking_nodes: bool
    enable_update_checks: bool
    enable_notifications: bool
    vibe_code_enabled: bool
    models: list[ModelConfigView]
    transcribe_models: list[str]
    tts_models: list[str]
    transcription: TranscriptionConfigView
    speech: SpeechConfigView
    validation_warnings: list[str]

    def model_display_name(self, alias: str) -> str:
        """User-facing name for a configured alias, or the alias when unknown."""
        return next(
            (model.display_name for model in self.models if model.alias == alias), alias
        )

    @property
    def default_model_display_name(self) -> str:
        return self.model_display_name(self.default_model_alias)
