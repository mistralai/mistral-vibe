//! The speech request matches what Python's Mistral SDK sends for the narrator.

use serde_json::json;
use vibe_rs::tts::{audio_request_metadata, decode_audio, status_error_type, SpeechConfig};

fn runtime() -> serde_json::Value {
    json!({"runtime": {"config": {"speech": {
        "model": {"name": "voxtral-mini-tts", "voice": "gb_jane_neutral", "responseFormat": "wav"},
        "provider": {"apiBase": "https://api.mistral.ai/", "apiKeyEnvVar": "MISTRAL_API_KEY", "client": "mistral"},
    }}}})
}

#[test]
fn speech_config_projects_from_the_runtime_snapshot() {
    let cfg = SpeechConfig::from_runtime(&runtime()).unwrap();
    assert_eq!(
        cfg,
        SpeechConfig {
            name: "voxtral-mini-tts".into(),
            voice: "gb_jane_neutral".into(),
            response_format: "wav".into(),
            api_base: "https://api.mistral.ai/".into(),
            api_key_env_var: "MISTRAL_API_KEY".into(),
        }
    );
    assert_eq!(cfg.url(), "https://api.mistral.ai/v1/audio/speech");
    assert_eq!(
        SpeechConfig::from_runtime(&json!({"runtime": {"config": {}}})),
        None
    );
}

#[test]
fn request_body_matches_the_sdk_speech_request() {
    let cfg = SpeechConfig::from_runtime(&runtime()).unwrap();
    assert_eq!(
        cfg.request_body("Done.", json!({"session_id": "s1"})),
        json!({
            "model": "voxtral-mini-tts",
            "input": "Done.",
            "metadata": {"session_id": "s1"},
            "stream": false,
            "voice_id": "gb_jane_neutral",
            "response_format": "wav",
        })
    );
}

#[test]
fn metadata_tags_the_call_as_a_secondary_vibe_code_call() {
    let metadata = audio_request_metadata("s1");
    assert_eq!(metadata["session_id"], "s1");
    assert_eq!(metadata["call_type"], "secondary_call");
    assert_eq!(metadata["call_source"], "vibe_code");
    assert_eq!(metadata["version"], env!("CARGO_PKG_VERSION"));
    assert_ne!(metadata["os"], "macos");
}

#[test]
fn audio_data_decodes_from_base64() {
    assert_eq!(
        decode_audio(&json!({"audio_data": "UklGRg=="})),
        Ok(b"RIFF".to_vec())
    );
    assert_eq!(
        decode_audio(&json!({"audio_data": "%%"}))
            .unwrap_err()
            .error_type,
        "Error"
    );
    assert_eq!(decode_audio(&json!({})).unwrap_err().error_type, "SDKError");
}

#[test]
fn http_failures_report_the_python_sdk_exception_name() {
    assert_eq!(status_error_type(422), "HTTPValidationError");
    assert_eq!(status_error_type(401), "SDKError");
    assert_eq!(status_error_type(500), "SDKError");
}
