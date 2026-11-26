//! Text-to-speech over the Mistral speech API.

use std::time::Duration;

use base64::Engine as _;
use serde_json::{json, Value};

use crate::audio_player::AudioError;

const TIMEOUT: Duration = Duration::from_secs(60);
const SDK_ERROR: &str = "SDKError";

/// The server's `SpeechConfigView`, flattened to the fields the client sends.
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
        let speech = crate::server::RuntimeSnapshot::config(runtime)?.speech?;
        Some(Self {
            name: speech.model.name?,
            voice: speech.model.voice?,
            response_format: speech
                .model
                .response_format
                .unwrap_or_else(|| "wav".to_owned()),
            api_base: speech.provider.api_base?,
            api_key_env_var: speech.provider.api_key_env_var,
        })
    }

    /// The SDK joins the provider's `api_base` (its `server_url`) with the speech path.
    pub fn url(&self) -> String {
        format!("{}/v1/audio/speech", self.api_base.trim_end_matches('/'))
    }

    /// The `SpeechRequest` body the Mistral SDK's `audio.speech.complete_async` sends.
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

/// Audio request metadata, minus the fields this client does not track.
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

/// Synthesize `text`: the decoded audio bytes.
pub async fn speak(
    cfg: &SpeechConfig,
    credential_client: Option<&crate::server::Client>,
    api_key: &str,
    text: &str,
    metadata: Value,
) -> Result<Vec<u8>, AudioError> {
    let oidc = api_key == crate::credentials::oidc::API_KEY_MARKER;
    let client = build_http_client(oidc).map_err(|e| AudioError::new(SDK_ERROR, e))?;
    let user_agent = concat!(
        "mistral-client-python/Mistral-Vibe/",
        env!("CARGO_PKG_VERSION")
    );
    let headers = crate::credentials::oidc::headers(credential_client, &cfg.url(), api_key)
        .await
        .map_err(AudioError::backend)?;
    let mut request = client.post(cfg.url());
    for (name, value) in headers {
        request = request.header(name, value);
    }
    let response = request
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

/// The error type for a failed request, named after the Mistral SDK's httpx exceptions.
fn request_error_type(error: &reqwest::Error) -> &'static str {
    if error.is_timeout() {
        "ReadTimeout"
    } else if error.is_connect() {
        "ConnectError"
    } else {
        SDK_ERROR
    }
}

/// The Mistral SDK error names: `HTTPValidationError` on 422 and `SDKError` otherwise.
pub fn status_error_type(status: u16) -> &'static str {
    if status == 422 {
        "HTTPValidationError"
    } else {
        SDK_ERROR
    }
}

/// ADR 0015: the speech client verifies against the effective trust policy,
/// with the same proxy handling as the shared builder; OIDC tokens never follow redirects.
pub fn build_http_client(oidc: bool) -> Result<reqwest::Client, String> {
    let mut builder =
        crate::utils::system_proxy::apply(reqwest::Client::builder().timeout(TIMEOUT));
    if oidc {
        builder = builder.redirect(reqwest::redirect::Policy::none());
    }
    crate::utils::tls::apply_tls_trust(builder)
        .build()
        .map_err(|e| format!("TTS client failed to build: {e}"))
}

/// The `SpeechResponse.audio_data` payload, base64-decoded.
pub fn decode_audio(body: &Value) -> Result<Vec<u8>, AudioError> {
    let data = body
        .get("audio_data")
        .and_then(Value::as_str)
        .ok_or_else(|| AudioError::new(SDK_ERROR, "TTS response has no audio_data".to_owned()))?;
    base64::engine::general_purpose::STANDARD
        .decode(data)
        .map_err(|e| AudioError::decode(format!("TTS audio_data is not base64: {e}")))
}
