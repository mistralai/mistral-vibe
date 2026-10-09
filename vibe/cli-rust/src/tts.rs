//! Text-to-speech over the Mistral speech API (Python `MistralTTSClient`).

use std::time::Duration;

use base64::Engine as _;
use serde_json::{json, Value};

use crate::audio_player::AudioError;

const TIMEOUT: Duration = Duration::from_secs(60);
const SDK_ERROR: &str = "SDKError";

/// Python `SpeechConfigView`, flattened to the fields the client sends.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SpeechConfig {
    pub name: String,
    pub voice: String,
    pub response_format: String,
    pub api_base: String,
    pub api_key_env_var: String,
}

impl SpeechConfig {
    /// The `speech` projection of a `runtime` value; `None` when the server sends none.
    pub fn from_runtime(runtime: &Value) -> Option<Self> {
        let speech = runtime.pointer("/runtime/config/speech")?;
        let field = |pointer: &str| {
            speech
                .pointer(pointer)
                .and_then(Value::as_str)
                .map(str::to_owned)
        };
        Some(Self {
            name: field("/model/name")?,
            voice: field("/model/voice")?,
            response_format: field("/model/responseFormat").unwrap_or_else(|| "wav".to_owned()),
            api_base: field("/provider/apiBase")?,
            api_key_env_var: field("/provider/apiKeyEnvVar").unwrap_or_default(),
        })
    }

    /// The SDK joins the provider's `api_base` (its `server_url`) with the speech path.
    pub fn url(&self) -> String {
        format!("{}/v1/audio/speech", self.api_base.trim_end_matches('/'))
    }

    /// The `SpeechRequest` body Python's `audio.speech.complete_async` sends.
    pub fn request_body(&self, text: &str, metadata: Value) -> Value {
        json!({
            "model": self.name,
            "input": text,
            "metadata": metadata,
            "stream": false,
            "voice_id": self.voice,
            "response_format": self.response_format,
        })
    }
}

/// Python `build_audio_request_metadata`, minus the fields the Rust client does not track.
pub fn audio_request_metadata(session_id: &str) -> Value {
    let os = match std::env::consts::OS {
        "macos" => "darwin",
        other => other,
    };
    json!({
        "os": os,
        "version": env!("CARGO_PKG_VERSION"),
        "session_id": session_id,
        "call_type": "secondary_call",
        "call_source": "vibe_code",
    })
}

/// Python `MistralTTSClient.speak`: the decoded audio bytes.
pub async fn speak(
    cfg: &SpeechConfig,
    api_key: &str,
    text: &str,
    metadata: Value,
) -> Result<Vec<u8>, AudioError> {
    let client = build_http_client().map_err(|e| AudioError::new(SDK_ERROR, e))?;
    let user_agent = concat!(
        "mistral-client-python/Mistral-Vibe/",
        env!("CARGO_PKG_VERSION")
    );
    let response = client
        .post(cfg.url())
        .bearer_auth(api_key)
        .header(reqwest::header::USER_AGENT, user_agent)
        .json(&cfg.request_body(text, metadata))
        .send()
        .await
        .map_err(|e| AudioError::new(request_error_type(&e), format!("TTS request failed: {e}")))?;
    let status = response.status();
    if !status.is_success() {
        return Err(AudioError::new(
            status_error_type(status.as_u16()),
            format!("TTS request failed: HTTP {status}"),
        ));
    }
    let body: Value = response
        .json()
        .await
        .map_err(|e| AudioError::new(SDK_ERROR, format!("TTS response is not JSON: {e}")))?;
    decode_audio(&body)
}

/// The httpx exception Python's SDK lets through for a failed request.
fn request_error_type(error: &reqwest::Error) -> &'static str {
    if error.is_timeout() {
        "ReadTimeout"
    } else if error.is_connect() {
        "ConnectError"
    } else {
        SDK_ERROR
    }
}

/// Python's SDK raises `HTTPValidationError` on 422 and `SDKError` otherwise.
pub fn status_error_type(status: u16) -> &'static str {
    if status == 422 {
        "HTTPValidationError"
    } else {
        SDK_ERROR
    }
}

/// ADR 0015: the speech client verifies against the effective trust policy.
pub fn build_http_client() -> Result<reqwest::Client, String> {
    let builder = reqwest::Client::builder().timeout(TIMEOUT);
    crate::utils::tls::apply_tls_trust(builder)
        .build()
        .map_err(|e| format!("TTS client failed to build: {e}"))
}

/// The `SpeechResponse.audio_data` payload, base64-decoded (`binascii.Error` in Python).
pub fn decode_audio(body: &Value) -> Result<Vec<u8>, AudioError> {
    let data = body
        .get("audio_data")
        .and_then(Value::as_str)
        .ok_or_else(|| AudioError::new(SDK_ERROR, "TTS response has no audio_data".to_owned()))?;
    base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|e| AudioError::decode(format!("TTS audio_data is not base64: {e}")))
}
