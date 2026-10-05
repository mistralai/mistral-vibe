//! The wizard's `setup/*` RPC calls (Python app-server `_setup.py`): the
//! client's ONLY persistence channel — `status` seeds the wizard,
//! `store-credential` saves the key, `submit-choices` lands the provider,
//! base URLs, and theme. No local fallback exists when these are absent
//! (ADR 0016): an old server fails the wizard with a clear error.

use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::server::{method, Client, RpcError};

/// The onboarding surface of a resolved provider (`SetupProviderView`): the
/// six fields the wizard reads and mutates, sent back on completion. The
/// wire is camelCase (`serialize_by_alias`, `to_camel`) — the Rust names
/// stay snake.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ProviderView {
    pub name: String,
    pub api_base: String,
    #[serde(default)]
    pub api_key_env_var: String,
    #[serde(default)]
    pub browser_auth_base_url: Option<String>,
    #[serde(default)]
    pub browser_auth_api_base_url: Option<String>,
    #[serde(default)]
    pub browser_auth_allow_origin_rewrite: bool,
}

/// `setup/status`'s params: the wizard names the provider the failed
/// session used (Python resolves it against the full effective config);
/// absent means the server's own active provider.
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StatusParams<'a> {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<&'a str>,
}

/// The wizard seed (`SetupStatusResponse`): the server's fully-resolved view.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupStatus {
    pub provider: ProviderView,
    pub console_base_url: String,
    pub vibe_base_url: String,
    pub theme: String,
    /// The server's browser-sign-in answer, which also weighs the
    /// provider's backend — not a URL-presence guess.
    pub supports_browser_sign_in: bool,
    pub has_api_key: bool,
    /// The server-resolved TLS trust flag (ADR 0015): the fully-resolved
    /// effective config, not just env var and user config.toml.
    #[serde(default)]
    pub enable_system_trust_store: bool,
}

/// `setup/store-credential`'s outcome, the Python key-persist contract
/// (`SetupStoreCredentialResponse`: the `outcome` tag plus `detail`).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum StoreOutcome {
    /// The key is stored (keyring or `.env`), and the server's own process
    /// env carries it — the reused child's session authenticates.
    Completed,
    /// The derived env var name is empty or invalid; nothing was saved
    /// (Python `env_var_error` — exit 1). `detail` carries the env var name.
    EnvVarError {
        #[serde(default)]
        detail: String,
    },
    /// Neither the keyring nor the `.env` fallback could store it, so the
    /// key lives only in the server process env for this run (Python
    /// `save_error` — warning, then continue). `detail` carries the error.
    SaveError {
        #[serde(default)]
        detail: String,
    },
}

/// `setup/submit-choices`'s outcome (`SetupSubmitChoicesResponse`: the
/// `outcome` tag plus `failures`).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum SubmitOutcome {
    Completed,
    /// A provider/URL/theme field failed to persist (Python
    /// `provider_config_error` — warning, then continue).
    ProviderConfigError {
        #[serde(default)]
        failures: Vec<String>,
    },
}

/// The wizard's `setup/submit-choices` fields; absent means "keep the
/// config" (the server upserts only on its own drift check).
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SubmitChoices {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<ProviderView>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub console_base_url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vibe_base_url: Option<String>,
    /// Present only when a theme was selected; presence is the write signal.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub theme: Option<String>,
}

/// `setup/store-credential`'s params (`SetupStoreCredentialParams`):
/// `provider` is a single word, the other two ride the camelCase wire.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct StoreCredential<'a> {
    pub provider: &'a str,
    pub api_key: &'a str,
    pub custom_domain: bool,
}

/// A `setup/*` call failure: `Unavailable` is the old-server skew (the
/// method family is absent) and never falls back to a local write.
#[derive(Debug)]
pub enum SetupError {
    Unavailable,
    Failed(String),
}

/// The wizard seed snapshot, from the server's resolved view; `provider`
/// names the provider the failed session used (the runtime's own
/// `_named_provider` resolution), if any.
pub async fn status(client: &Client, provider: Option<&str>) -> Result<SetupStatus, SetupError> {
    let value = call(
        client,
        method::SETUP_STATUS,
        json!(StatusParams { provider }),
    )
    .await?;
    serde_json::from_value(value).map_err(|error| SetupError::Failed(error.to_string()))
}

/// Store the API key server-side: the server derives the env var from its
/// own resolved config and owns the keyring/`.env` writes.
pub async fn store_credential(
    client: &Client,
    provider: &str,
    api_key: &str,
    custom_domain: bool,
) -> Result<StoreOutcome, SetupError> {
    let params = serde_json::to_value(StoreCredential {
        provider,
        api_key,
        custom_domain,
    })
    .map_err(|error| SetupError::Failed(error.to_string()))?;
    let value = call(client, method::SETUP_STORE_CREDENTIAL, params).await?;
    serde_json::from_value(value).map_err(|error| SetupError::Failed(error.to_string()))
}

/// Land the wizard's choices; absent fields mean "keep the config".
pub async fn submit_choices(
    client: &Client,
    choices: &SubmitChoices,
) -> Result<SubmitOutcome, SetupError> {
    let params =
        serde_json::to_value(choices).map_err(|error| SetupError::Failed(error.to_string()))?;
    let value = call(client, method::SETUP_SUBMIT_CHOICES, params).await?;
    serde_json::from_value(value).map_err(|error| SetupError::Failed(error.to_string()))
}

/// One request with the old-server skew classified.
async fn call(
    client: &Client,
    method_name: &str,
    params: serde_json::Value,
) -> Result<serde_json::Value, SetupError> {
    match client.request_err(method_name, params).await {
        Ok(value) => Ok(value),
        Err(error) if is_method_not_found(&error) => Err(SetupError::Unavailable),
        Err(error) => Err(SetupError::Failed(format!(
            "[{}] {}",
            error.code(),
            error.message
        ))),
    }
}

/// `method_not_found` (app-server `ProtocolErrorCode`) or JSON-RPC `-32601`.
fn is_method_not_found(error: &RpcError) -> bool {
    error.code.as_str() == Some("method_not_found") || error.code.as_i64() == Some(-32601)
}
