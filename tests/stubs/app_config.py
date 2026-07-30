from __future__ import annotations

from vibe.app_server.config import (
    AudioProviderView,
    ConfigView,
    ModelConfigView,
    SpeechConfigView,
    TranscribeModelConfigView,
    TranscriptionConfigView,
    TTSModelConfigView,
)


def build_test_app_config(
    *,
    voice_mode_enabled: bool = False,
    narrator_enabled: bool = False,
    show_thinking_nodes: bool = False,
) -> ConfigView:
    return ConfigView(
        active_model=ModelConfigView(
            name="test-model", alias="test-model", thinking="off", supports_images=False
        ),
        theme="textual-dark",
        disable_welcome_banner_animation=False,
        autocopy_to_clipboard=True,
        file_watcher_for_autocomplete=False,
        ask_confirmation_on_exit=True,
        voice_mode_enabled=voice_mode_enabled,
        narrator_enabled=narrator_enabled,
        show_thinking_nodes=show_thinking_nodes,
        enable_update_checks=True,
        enable_notifications=True,
        vibe_code_enabled=True,
        models=[
            ModelConfigView(
                name="test-model",
                alias="test-model",
                thinking="off",
                supports_images=False,
            )
        ],
        transcribe_models=["test-transcribe"],
        tts_models=["test-tts"],
        transcription=TranscriptionConfigView(
            model=TranscribeModelConfigView(
                name="test-transcribe",
                sample_rate=16_000,
                encoding="pcm_s16le",
                language="en",
                target_streaming_delay_ms=500,
            ),
            provider=AudioProviderView(
                api_base="wss://api.mistral.ai",
                api_key_env_var="MISTRAL_API_KEY",
                client="mistral",
            ),
        ),
        speech=SpeechConfigView(
            model=TTSModelConfigView(
                name="test-tts", voice="gb_jane_neutral", response_format="wav"
            ),
            provider=AudioProviderView(
                api_base="https://api.mistral.ai",
                api_key_env_var="MISTRAL_API_KEY",
                client="mistral",
            ),
        ),
        validation_warnings=[],
    )
