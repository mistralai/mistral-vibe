//! Sign-in flow orchestration: events, PKCE, polling, and URL validation.

use std::time::Duration;

use tokio::sync::mpsc;

use super::sign_in_gateway::{SignInError, SignInErrorCode, SignInGateway};

/// Status events emitted during the sign-in flow.
#[derive(Debug, Clone)]
pub enum SignInStatus {
    OpeningBrowser,
    Waiting,
    Exchanging,
    Completed,
}

/// Events from the browser sign-in flow.
#[derive(Debug, Clone)]
pub enum SignInEvent {
    Started { sign_in_url: String },
    StatusChanged(SignInStatus),
    Completed { api_key: String },
    Failed { message: String },
}

/// Run the full browser sign-in flow, emitting events via `tx`. The key
/// travels in `Completed`; the `Err` is surfaced as `Failed` by the spawn
/// wrapper (browser_sign_in), which owns error reporting.
pub async fn run_sign_in(
    gateway: SignInGateway,
    tx: mpsc::Sender<SignInEvent>,
) -> Result<(), SignInError> {
    if crate::utils::is_replaying() {
        return replay_sign_in(tx).await;
    }
    let (verifier, challenge) = generate_pkce_pair();
    let process = gateway.create_process(&challenge).await?;
    let _ = tx
        .send(SignInEvent::Started {
            sign_in_url: process.sign_in_url.clone(),
        })
        .await;
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::OpeningBrowser))
        .await;
    crate::external_url::open(&process.sign_in_url);
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::Waiting))
        .await;
    let exchange_token =
        poll_for_completion(&gateway, &process.poll_url, &process.expires_at).await?;
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::Exchanging))
        .await;
    let api_key = gateway
        .exchange(&process.process_id, &exchange_token, &verifier)
        .await?;
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::Completed))
        .await;
    // Python `SUCCESS_EXIT_DELAY_SECONDS`: the Finished step shows "Sign-in
    // complete" for two seconds before the wizard moves on.
    tokio::time::sleep(Duration::from_secs(2)).await;
    let _ = tx.send(SignInEvent::Completed { api_key }).await;
    Ok(())
}

/// The replay harness has no network: script the flow up to the waiting step
/// so captures of the step-tracker screen stay deterministic. The wizard's
/// replay loop freezes right there, pending forever.
async fn replay_sign_in(tx: mpsc::Sender<SignInEvent>) -> Result<(), SignInError> {
    const SIGN_IN_URL: &str =
        "https://console.mistral.ai/codestral/cli/authenticate?process_id=replay";
    let _ = tx
        .send(SignInEvent::Started {
            sign_in_url: SIGN_IN_URL.to_owned(),
        })
        .await;
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::OpeningBrowser))
        .await;
    crate::external_url::open(SIGN_IN_URL);
    let _ = tx
        .send(SignInEvent::StatusChanged(SignInStatus::Waiting))
        .await;
    Ok(())
}

/// Poll until completion, expired, or error. The server advertises the
/// sign-in deadline (`expires_at`); polling stops when it passes, like
/// Python's `expires_at` check in `authenticate`. After three consecutive
/// failures the error propagates: the spawn wrapper turns it into the
/// `Failed` event.
async fn poll_for_completion(
    gateway: &SignInGateway,
    poll_url: &str,
    expires_at: &time::OffsetDateTime,
) -> Result<String, SignInError> {
    let mut failures = 0u32;
    loop {
        if time::OffsetDateTime::now_utc() >= *expires_at {
            return Err(SignInError {
                message: "Browser sign-in expired.".into(),
                code: SignInErrorCode::PollFailed,
            });
        }
        match gateway.poll(poll_url).await {
            Ok(result) => {
                failures = 0;
                match result.status.as_str() {
                    "pending" => {
                        tokio::time::sleep(Duration::from_secs(3)).await;
                    }
                    "completed" => {
                        return result.exchange_token.ok_or(SignInError {
                            message: "Missing exchange token.".into(),
                            code: SignInErrorCode::ExchangeFailed,
                        });
                    }
                    "expired" => {
                        return Err(SignInError {
                            message: "Browser sign-in expired.".into(),
                            code: SignInErrorCode::PollFailed,
                        });
                    }
                    "denied" => {
                        return Err(SignInError {
                            message: "Browser sign-in was denied.".into(),
                            code: SignInErrorCode::PollFailed,
                        });
                    }
                    "error" => {
                        return Err(SignInError {
                            message: result.message.unwrap_or_else(|| "Sign-in failed.".into()),
                            code: SignInErrorCode::PollFailed,
                        });
                    }
                    _ => {
                        return Err(SignInError {
                            message: "Unknown poll status.".into(),
                            code: SignInErrorCode::PollFailed,
                        });
                    }
                }
            }
            Err(e) if e.code == SignInErrorCode::PollFailed => {
                failures += 1;
                if failures >= 3 {
                    return Err(e);
                }
                tokio::time::sleep(Duration::from_secs(3)).await;
            }
            Err(e) => return Err(e),
        }
    }
}

/// Generate a PKCE code verifier and challenge pair.
fn generate_pkce_pair() -> (String, String) {
    use sha2::{Digest, Sha256};
    let verifier = generate_code_verifier();
    let digest = Sha256::digest(verifier.as_bytes());
    let challenge = base64url(&digest);
    (verifier, challenge)
}

fn generate_code_verifier() -> String {
    // Python `secrets.token_urlsafe(64)`: 64 random bytes, base64url. A broken
    // system RNG has no safe fallback; fail instead of guessing.
    let mut bytes = [0u8; 64];
    getrandom::fill(&mut bytes).expect("system RNG unavailable");
    base64url(&bytes)
}

fn base64url(input: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(input)
}
