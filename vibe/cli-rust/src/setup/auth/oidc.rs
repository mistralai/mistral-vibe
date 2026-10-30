//! Cancellable OIDC orchestration; credentials stay on the app-server.

use std::sync::Arc;
use std::time::Duration;

use serde::Deserialize;
use serde_json::json;
use tokio::sync::{mpsc, oneshot};
use tokio::time::{timeout, timeout_at, Instant};

use super::browser_sign_in::SignInHandle;
use super::rpc::{call, SetupError};
use super::sign_in_flow::{SignInEvent, SignInStatus};
use crate::server::Client;

pub const SETUP_OIDC_START: &str = "setup/oidc-start";
pub const SETUP_OIDC_POLL: &str = "setup/oidc-poll";
pub const SETUP_OIDC_CANCEL: &str = "setup/oidc-cancel";
pub const FLOW_TIMEOUT: Duration = Duration::from_secs(300);
pub const POLL_INTERVAL: Duration = Duration::from_millis(250);
const CLEANUP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OidcStart {
    pub attempt_id: String,
}

#[derive(Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OidcStatus {
    Waiting,
    Exchanging,
    Completed,
    Failed,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct OidcPoll {
    pub status: OidcStatus,
    pub authorization_url: Option<String>,
    pub detail: Option<String>,
}

pub async fn start(client: &Client) -> Result<OidcStart, SetupError> {
    let value = call(client, SETUP_OIDC_START, json!({})).await?;
    serde_json::from_value(value).map_err(|error| SetupError::Failed(error.to_string()))
}

pub async fn poll(client: &Client, attempt_id: &str) -> Result<OidcPoll, SetupError> {
    let value = call(client, SETUP_OIDC_POLL, json!({"attemptId": attempt_id})).await?;
    serde_json::from_value(value).map_err(|error| SetupError::Failed(error.to_string()))
}

pub fn spawn(client: Arc<Client>) -> SignInHandle {
    let (tx, rx) = mpsc::channel(16);
    let (cancel_tx, cancel_rx) = oneshot::channel();
    tokio::spawn(async move {
        if let Err(message) = run(client, &tx, cancel_rx).await {
            let _ = tx.send(SignInEvent::Failed { message }).await;
        }
    });
    SignInHandle::oidc(rx, cancel_tx)
}

async fn run(
    client: Arc<Client>,
    tx: &mpsc::Sender<SignInEvent>,
    mut cancel: oneshot::Receiver<()>,
) -> Result<(), String> {
    let deadline = Instant::now() + FLOW_TIMEOUT;
    // Learn the attempt ID even when cancellation is queued during start.
    let response = timeout_at(deadline, start(&client))
        .await
        .map_err(|_| "Browser sign-in expired.".to_owned())?
        .map_err(error_message)?;
    let mut attempt = Attempt {
        client,
        id: Some(response.attempt_id),
    };
    tokio::select! {
        biased;
        _ = &mut cancel => return Ok(()),
        result = timeout_at(deadline, authenticate(&mut attempt, tx)) => {
            result.map_err(|_| "Browser sign-in expired.".to_owned())??;
        }
    }
    tokio::select! {
        biased;
        _ = &mut cancel => Ok(()),
        _ = tokio::time::sleep(Duration::from_secs(2)) => emit(tx, SignInEvent::OidcCompleted).await,
    }
}

async fn authenticate(attempt: &mut Attempt, tx: &mpsc::Sender<SignInEvent>) -> Result<(), String> {
    let mut opened = false;
    loop {
        let result = poll(
            &attempt.client,
            attempt.id.as_deref().expect("active attempt"),
        )
        .await
        .map_err(error_message)?;
        if let Some(url) = result.authorization_url.filter(|_| !opened) {
            emit(
                tx,
                SignInEvent::Started {
                    sign_in_url: url.clone(),
                },
            )
            .await?;
            status(tx, SignInStatus::OpeningBrowser).await?;
            crate::external_url::open(&url);
            status(tx, SignInStatus::Waiting).await?;
            opened = true;
        }
        match result.status {
            OidcStatus::Waiting => {}
            OidcStatus::Exchanging => status(tx, SignInStatus::Exchanging).await?,
            OidcStatus::Failed => {
                return Err(result.detail.unwrap_or_else(|| "Sign-in failed.".into()))
            }
            OidcStatus::Completed => {
                attempt.id = None;
                status(tx, SignInStatus::Completed).await?;
                return Ok(());
            }
        }
        tokio::time::sleep(POLL_INTERVAL).await;
    }
}

async fn status(tx: &mpsc::Sender<SignInEvent>, value: SignInStatus) -> Result<(), String> {
    emit(tx, SignInEvent::StatusChanged(value)).await
}

async fn emit(tx: &mpsc::Sender<SignInEvent>, value: SignInEvent) -> Result<(), String> {
    tx.send(value)
        .await
        .map_err(|_| "Sign-in cancelled.".to_owned())
}

fn error_message(error: SetupError) -> String {
    match error {
        SetupError::Unavailable => "This app-server does not support OIDC sign-in.".into(),
        SetupError::Failed(message) => message,
    }
}

struct Attempt {
    client: Arc<Client>,
    id: Option<String>,
}

impl Drop for Attempt {
    fn drop(&mut self) {
        if let Some(id) = self.id.take() {
            // Queue cancellation now; only its acknowledgement needs a bounded task.
            if let Ok(reply) = self
                .client
                .send_now(SETUP_OIDC_CANCEL, json!({"attemptId": id}))
            {
                tokio::spawn(async move {
                    let _ = timeout(CLEANUP_TIMEOUT, reply).await;
                });
            }
        }
    }
}
