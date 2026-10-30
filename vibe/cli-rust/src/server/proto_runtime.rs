//! Typed views of `RuntimeSnapshot` and `ConfigView` (app-server `protocol.py`, `config.py`).

use serde::Deserialize;
use serde_json::Value;

use super::{AgentSummary, SessionStats};

#[derive(Default, Deserialize)]
#[serde(default)]
struct Envelope {
    runtime: Option<RuntimeSnapshot>,
}

/// The fields the client reads; every one defaults (ADR 0014).
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct RuntimeSnapshot {
    pub config: Option<ConfigView>,
    pub stats: SessionStats,
    pub context_window: u64,
    #[serde(deserialize_with = "crate::utils::json::lenient")]
    pub issues: Vec<ConfigIssue>,
    /// `None` when the server sent no list.
    #[serde(deserialize_with = "crate::utils::json::lenient_opt")]
    pub agents: Option<Vec<AgentSummary>>,
}

impl RuntimeSnapshot {
    /// The `runtime` of a `{runtime: {...}}` response, `None` when it carries none.
    pub fn read(response: &Value) -> Option<Self> {
        crate::utils::json::read::<Envelope>(response).runtime
    }

    /// The `runtime.config` of a `{runtime: {...}}` response.
    pub fn config(response: &Value) -> Option<ConfigView> {
        Self::read(response)?.config
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ConfigIssue {
    #[serde(default)]
    pub file: String,
    pub message: String,
}

fn yes() -> bool {
    true
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ConfigView {
    pub active_model: Option<ModelView>,
    #[serde(default)]
    pub active_model_pinned: bool,
    pub images_supported: Option<bool>,
    #[serde(default)]
    pub default_model_alias: String,
    pub default_agent: Option<String>,
    pub theme: Option<String>,
    pub log_level: Option<String>,
    #[serde(default)]
    pub disable_welcome_banner_animation: bool,
    #[serde(default = "yes")]
    pub cursor_blink: bool,
    #[serde(default = "yes")]
    pub show_greeting: bool,
    #[serde(default = "yes")]
    pub autocopy_to_clipboard: bool,
    #[serde(default = "yes")]
    pub enable_mouse_capture: bool,
    #[serde(default = "yes")]
    pub ask_confirmation_on_exit: bool,
    pub voice_mode_enabled: Option<bool>,
    #[serde(default)]
    pub narrator_enabled: bool,
    #[serde(default = "yes")]
    pub show_thinking_nodes: bool,
    #[serde(default = "yes")]
    pub show_subagent_status_list: bool,
    #[serde(default = "yes")]
    pub enable_update_checks: bool,
    #[serde(default = "yes")]
    pub enable_notifications: bool,
    #[serde(default)]
    pub enable_system_trust_store: bool,
    #[serde(default = "yes")]
    pub experimental_enable_tab_status: bool,
    /// Absent on an older server: stay silent rather than assume consent.
    #[serde(default)]
    pub enable_telemetry: bool,
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub models: Vec<ModelView>,
    pub transcription: Option<AudioView>,
    pub speech: Option<AudioView>,
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub validation_warnings: Vec<String>,
}

impl Default for ConfigView {
    fn default() -> Self {
        Self::deserialize(&serde_json::json!({})).expect("every field defaults")
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct ModelView {
    pub alias: String,
    pub display_name: Option<String>,
    pub thinking: Option<String>,
    pub supports_images: Option<bool>,
    #[serde(deserialize_with = "crate::utils::json::lenient")]
    pub thinking_levels: Vec<String>,
}

/// `TranscriptionConfigView` or `SpeechConfigView`: a model and its provider.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct AudioView {
    pub model: AudioModelView,
    pub provider: AudioProviderView,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct AudioModelView {
    pub name: Option<String>,
    pub voice: Option<String>,
    pub response_format: Option<String>,
    pub sample_rate: Option<u32>,
    pub encoding: Option<String>,
    pub target_streaming_delay_ms: Option<u64>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct AudioProviderView {
    pub api_base: Option<String>,
    pub api_key_env_var: String,
}
