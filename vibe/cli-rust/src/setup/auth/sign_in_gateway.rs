//! HTTP gateway for browser sign-in (Python `HttpBrowserSignInGateway`).

use std::time::Duration;

use serde::Deserialize;

use super::sign_in_url::validate_url;

/// Error from the browser sign-in gateway.
#[derive(Debug, Clone)]
pub struct SignInError {
    pub message: String,
    pub code: SignInErrorCode,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SignInErrorCode {
    StartFailed,
    PollFailed,
    ExchangeFailed,
    MissingApiKey,
}

/// A created sign-in process.
#[derive(Debug, Clone)]
pub struct SignInProcess {
    pub process_id: String,
    pub sign_in_url: String,
    pub poll_url: String,
    /// The server-side sign-in deadline (Python `expires_at`).
    pub expires_at: time::OffsetDateTime,
}

/// Poll result status.
#[derive(Debug, Clone)]
pub struct PollResult {
    pub status: String,
    pub exchange_token: Option<String>,
    pub message: Option<String>,
}

/// Gateway client for the browser sign-in endpoints.
pub struct SignInGateway {
    browser_base_url: String,
    api_base_url: String,
    /// Split-horizon sign-in: sign-in URLs may legitimately live on another
    /// origin than the console base (Python `allow_origin_rewrite`).
    allow_origin_rewrite: bool,
    client: reqwest::Client,
}

impl SignInGateway {
    pub fn new(
        browser_base_url: &str,
        api_base_url: &str,
        allow_origin_rewrite: bool,
        enable_system_trust_store: bool,
    ) -> Result<Self, SignInError> {
        let client = crate::update_notifier::gateway::wizard_http_client(
            Duration::from_secs(30),
            enable_system_trust_store,
        )
        .map_err(|e| SignInError {
            message: format!("Failed to start browser sign-in: {e}"),
            code: SignInErrorCode::StartFailed,
        })?;
        Ok(Self {
            browser_base_url: browser_base_url.trim_end_matches('/').to_owned(),
            api_base_url: api_base_url.trim_end_matches('/').to_owned(),
            allow_origin_rewrite,
            client,
        })
    }

    /// Create a sign-in process with a PKCE code challenge.
    pub async fn create_process(&self, code_challenge: &str) -> Result<SignInProcess, SignInError> {
        let url = format!("{}/vibe/sign-in", self.api_base_url);
        let response = self
            .client
            .post(&url)
            .json(&serde_json::json!({
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }))
            .send()
            .await
            .map_err(|e| SignInError {
                message: format!("Failed to start browser sign-in: {e}"),
                code: SignInErrorCode::StartFailed,
            })?;
        if !response.status().is_success() {
            return Err(SignInError {
                message: "Failed to start browser sign-in.".into(),
                code: SignInErrorCode::StartFailed,
            });
        }
        let payload: CreateProcessResponse = response.json().await.map_err(|_| SignInError {
            message: "Failed to parse sign-in response.".into(),
            code: SignInErrorCode::StartFailed,
        })?;
        let sign_in_url = validate_url(
            &payload.sign_in_url,
            &self.browser_base_url,
            self.allow_origin_rewrite,
            SignInErrorCode::StartFailed,
        )?;
        let poll_url = validate_url(
            &payload.poll_url,
            &self.api_base_url,
            self.allow_origin_rewrite,
            SignInErrorCode::StartFailed,
        )?;
        // Python `_parse_expires_at`: RFC 3339 with a Z suffix.
        let expires_at = time::OffsetDateTime::parse(
            &payload.expires_at,
            &time::format_description::well_known::Rfc3339,
        )
        .map_err(|_| SignInError {
            message: "Failed to parse sign-in response.".into(),
            code: SignInErrorCode::StartFailed,
        })?;
        Ok(SignInProcess {
            process_id: payload.process_id,
            sign_in_url,
            poll_url,
            expires_at,
        })
    }

    /// Poll the sign-in status.
    pub async fn poll(&self, poll_url: &str) -> Result<PollResult, SignInError> {
        let poll_url = validate_url(
            poll_url,
            &self.api_base_url,
            self.allow_origin_rewrite,
            SignInErrorCode::PollFailed,
        )?;
        let response = self
            .client
            .get(&poll_url)
            .send()
            .await
            .map_err(|e| SignInError {
                message: format!("Browser sign-in poll failed: {e}"),
                code: SignInErrorCode::PollFailed,
            })?;
        if response.status().as_u16() == 410 {
            return Ok(PollResult {
                status: "expired".into(),
                exchange_token: None,
                message: None,
            });
        }
        if !response.status().is_success() {
            return Err(SignInError {
                message: "Browser sign-in poll failed.".into(),
                code: SignInErrorCode::PollFailed,
            });
        }
        let payload: PollResponse = response.json().await.map_err(|_| SignInError {
            message: "Failed to parse poll response.".into(),
            code: SignInErrorCode::PollFailed,
        })?;
        Ok(PollResult {
            status: payload.status,
            exchange_token: payload.exchange_token,
            message: payload.message,
        })
    }

    /// Exchange the token for an API key.
    pub async fn exchange(
        &self,
        process_id: &str,
        exchange_token: &str,
        code_verifier: &str,
    ) -> Result<String, SignInError> {
        let url = format!("{}/vibe/sign-in/{process_id}/exchange", self.api_base_url);
        let response = self
            .client
            .post(&url)
            .json(&serde_json::json!({
                "exchange_token": exchange_token,
                "code_verifier": code_verifier,
            }))
            .send()
            .await
            .map_err(|e| SignInError {
                message: format!("Exchange failed: {e}"),
                code: SignInErrorCode::ExchangeFailed,
            })?;
        if !response.status().is_success() {
            return Err(SignInError {
                message: "Failed to exchange sign-in for API key.".into(),
                code: SignInErrorCode::ExchangeFailed,
            });
        }
        let payload: ExchangeResponse = response.json().await.map_err(|_| SignInError {
            message: "Failed to parse exchange response.".into(),
            code: SignInErrorCode::ExchangeFailed,
        })?;
        payload.api_key.ok_or(SignInError {
            message: "Exchange did not return an API key.".into(),
            code: SignInErrorCode::MissingApiKey,
        })
    }
}

#[derive(Deserialize)]
struct CreateProcessResponse {
    process_id: String,
    sign_in_url: String,
    poll_url: String,
    /// RFC 3339; the server-side deadline for completing the sign-in.
    expires_at: String,
}

#[derive(Deserialize)]
struct PollResponse {
    status: String,
    #[serde(default)]
    exchange_token: Option<String>,
    #[serde(default)]
    message: Option<String>,
}

#[derive(Deserialize)]
struct ExchangeResponse {
    #[serde(default)]
    api_key: Option<String>,
}
