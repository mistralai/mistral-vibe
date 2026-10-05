//! Browser sign-in orchestration (Python `BrowserSignInService`): the
//! background task and its handle only — the wizard's view state folds the
//! events in `wizard::sign_in`, so this module knows nothing about the
//! wizard.

use tokio::sync::mpsc;

use super::sign_in_flow::{run_sign_in, SignInEvent};
use super::sign_in_gateway::SignInGateway;

/// The event receiver plus a way to stop the background flow. Respawning
/// the sign-in must abort the previous task: it keeps polling and can still
/// open a browser long after its receiver was replaced.
pub struct SignInHandle {
    rx: mpsc::Receiver<SignInEvent>,
    abort: tokio::task::AbortHandle,
}

impl SignInHandle {
    /// Next event from the sign-in flow, or `None` when it finished.
    pub async fn recv(&mut self) -> Option<SignInEvent> {
        self.rx.recv().await
    }

    /// Stop the background flow.
    pub fn abort(&self) {
        self.abort.abort();
    }
}

/// Spawn the browser sign-in flow as a background task on the provider's
/// sign-in endpoints: the browser console origin, its CLI-reachable API
/// base, and the split-horizon origin-rewrite flag.
pub fn spawn(
    browser_auth_base_url: &str,
    browser_auth_api_base_url: &str,
    allow_origin_rewrite: bool,
    enable_system_trust_store: bool,
) -> SignInHandle {
    let (tx, rx) = mpsc::channel::<SignInEvent>(16);
    let task_tx = tx.clone();
    // The gateway build can fail (e.g. a system trust store with no CA
    // certificates): fold it into the flow's own Failed event instead of
    // panicking inside the wizard.
    let task = match SignInGateway::new(
        browser_auth_base_url,
        browser_auth_api_base_url,
        allow_origin_rewrite,
        enable_system_trust_store,
    ) {
        Ok(gateway) => tokio::spawn(async move {
            if let Err(error) = run_sign_in(gateway, task_tx.clone()).await {
                let _ = task_tx
                    .send(SignInEvent::Failed {
                        message: error.message,
                    })
                    .await;
            }
        }),
        Err(error) => tokio::spawn(async move {
            let _ = task_tx
                .send(SignInEvent::Failed {
                    message: error.message,
                })
                .await;
        }),
    };
    SignInHandle {
        rx,
        abort: task.abort_handle(),
    }
}
