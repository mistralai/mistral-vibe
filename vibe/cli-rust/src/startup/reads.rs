//! The startup reads: one `config/read` and the `runtime/read` projections.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::server::{method, Client, RuntimeReadParams};
use crate::voice::TranscriptionConfig;

/// What the UI needs from the one startup `config/read`.
pub struct ConfigRead {
    pub voice_mode_enabled: bool,
    pub show_greeting: bool,
    pub transcription: Option<TranscriptionConfig>,
    pub log_level: Option<String>,
    pub enable_telemetry: bool,
}

impl Default for ConfigRead {
    fn default() -> Self {
        Self {
            voice_mode_enabled: false,
            show_greeting: true,
            transcription: None,
            log_level: None,
            // Absent on an older server: stay silent rather than assume consent.
            enable_telemetry: false,
        }
    }
}

/// Read voice mode, the greeting flag, and transcription once at startup.
pub(super) async fn read_config(client: &Arc<Client>, cwd: Option<String>) -> ConfigRead {
    let params = json!({ "cwd": cwd });
    let result = match client.request(method::CONFIG_READ, params).await {
        Ok(result) => result,
        Err(err) => {
            tracing::warn!(%err, "config/read failed; starting with defaults");
            return ConfigRead::default();
        }
    };
    let Some(config) = result.pointer("/config") else {
        tracing::warn!("config/read answered without a config; starting with defaults");
        return ConfigRead::default();
    };
    let enabled = config
        .get("voiceModeEnabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let show_greeting = config
        .get("showGreeting")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let transcription = config.get("transcription").map(|t| TranscriptionConfig {
        name: str_at(t, "/model/name", ""),
        sample_rate: t
            .pointer("/model/sampleRate")
            .and_then(Value::as_u64)
            .unwrap_or(16000) as u32,
        encoding: str_at(t, "/model/encoding", "pcm_s16le"),
        target_streaming_delay_ms: t
            .pointer("/model/targetStreamingDelayMs")
            .and_then(Value::as_u64)
            .unwrap_or(500),
        api_base: str_at(t, "/provider/apiBase", "wss://api.mistral.ai"),
        api_key_env_var: str_at(t, "/provider/apiKeyEnvVar", ""),
    });
    ConfigRead {
        voice_mode_enabled: enabled,
        show_greeting,
        transcription,
        log_level: config
            .get("logLevel")
            .and_then(Value::as_str)
            .map(str::to_owned),
        enable_telemetry: config
            .get("enableTelemetry")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    }
}

/// A JSON string at `pointer`, or `default` when absent.
fn str_at(value: &Value, pointer: &str, default: &str) -> String {
    value
        .pointer(pointer)
        .and_then(Value::as_str)
        .unwrap_or(default)
        .to_string()
}

/// Read the runtime snapshot once at startup, returning the raw `runtime/read` result.
pub(super) async fn read_runtime(client: &Arc<Client>, session_id: &str) -> Result<Value> {
    let params = RuntimeReadParams {
        session_id: session_id.to_owned(),
    };
    let value = serde_json::to_value(&params)?;
    client
        .request(method::RUNTIME_READ, value)
        .await
        .context("runtime/read")
}

/// Whether reasoning traces are shown, from a `runtime/read` result.
pub fn read_show_thinking_nodes(result: &Value) -> bool {
    result
        .pointer("/runtime/config/showThinkingNodes")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

pub fn read_tokens(result: &Value) -> (u64, u64) {
    let current = result
        .pointer("/runtime/stats/contextTokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let maximum = result
        .pointer("/runtime/contextWindow")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    (current, maximum)
}
