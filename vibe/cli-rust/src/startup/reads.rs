//! The startup reads: one `config/read` and the `runtime/read` projections.

use std::sync::Arc;

use anyhow::{Context, Result};
use serde_json::{json, Value};

use crate::server::{method, AudioView, Client, ConfigView, RuntimeReadParams, RuntimeSnapshot};
use crate::voice::TranscriptionConfig;

/// What the UI needs from the one startup `config/read`.
pub struct ConfigRead {
    pub voice_mode_enabled: bool,
    pub show_greeting: bool,
    pub transcription: Option<TranscriptionConfig>,
    pub log_level: Option<String>,
    pub enable_telemetry: bool,
    pub enable_update_checks: bool,
    pub enable_system_trust_store: bool,
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
            // Absent on an older server: keep checking, the config default.
            enable_update_checks: true,
            // Absent on an older server: the bundled roots, the config default.
            enable_system_trust_store: false,
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
    let Some(config) = result
        .get("config")
        .map(crate::utils::json::read::<ConfigView>)
    else {
        tracing::warn!("config/read answered without a config; starting with defaults");
        return ConfigRead::default();
    };
    ConfigRead {
        voice_mode_enabled: config.voice_mode_enabled.unwrap_or(false),
        show_greeting: config.show_greeting,
        transcription: config.transcription.map(transcription),
        log_level: config.log_level,
        enable_telemetry: config.enable_telemetry,
        enable_update_checks: config.enable_update_checks,
        enable_system_trust_store: config.enable_system_trust_store,
    }
}

fn transcription(view: AudioView) -> TranscriptionConfig {
    TranscriptionConfig {
        name: view.model.name.unwrap_or_default(),
        sample_rate: view.model.sample_rate.unwrap_or(16000),
        encoding: view.model.encoding.unwrap_or_else(|| "pcm_s16le".into()),
        target_streaming_delay_ms: view.model.target_streaming_delay_ms.unwrap_or(500),
        api_base: view
            .provider
            .api_base
            .unwrap_or_else(|| "wss://api.mistral.ai".into()),
        api_key_env_var: view.provider.api_key_env_var,
    }
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

fn config(result: &Value) -> ConfigView {
    RuntimeSnapshot::config(result).unwrap_or_default()
}

/// Whether reasoning traces are shown, from a `runtime/read` result.
pub fn read_show_thinking_nodes(result: &Value) -> bool {
    config(result).show_thinking_nodes
}

/// Whether the subagent status list is shown (config `show_subagent_status_list`).
pub fn read_show_subagent_status_list(result: &Value) -> bool {
    config(result).show_subagent_status_list
}

/// Whether Ctrl+D asks for a second press (config `ask_confirmation_on_exit`).
pub fn read_ask_confirmation_on_exit(result: &Value) -> bool {
    config(result).ask_confirmation_on_exit
}

pub fn read_tokens(result: &Value) -> (u64, u64) {
    let runtime = RuntimeSnapshot::read(result).unwrap_or_default();
    (runtime.stats.context_tokens, runtime.context_window)
}
