//! The default Mistral `setup/status` a fresh install resolves to — one
//! typed fixture shared by the wizard tests and the fake server's wire
//! answer. Included per test binary via `#[path]`.

use vibe_rs::setup::auth::rpc::{ProviderView, SetupStatus};

/// The server's resolved default view; `serde_json::to_value` of it is the
/// camelCase wire frame (pinned by `tests/units/setup_wire.rs`).
pub(crate) fn default_status() -> SetupStatus {
    SetupStatus {
        provider: ProviderView {
            name: "mistral".into(),
            api_base: "https://api.mistral.ai/v1".into(),
            api_key_env_var: "MISTRAL_API_KEY".into(),
            browser_auth_base_url: Some("https://console.mistral.ai".into()),
            browser_auth_api_base_url: Some("https://console.mistral.ai/api".into()),
            browser_auth_allow_origin_rewrite: false,
        },
        console_base_url: "https://console.mistral.ai".into(),
        vibe_base_url: "https://chat.mistral.ai".into(),
        theme: "auto".into(),
        supports_browser_sign_in: true,
        has_api_key: false,
        enable_system_trust_store: false,
    }
}
