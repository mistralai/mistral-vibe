//! Browser sign-in orchestration: the
//! background task and its handle only — the wizard's view state folds the
//! events in `wizard::sign_in`, so this module knows nothing about the
//! wizard.

use tokio::sync::{mpsc, oneshot};

use crate::work::Work;

use super::sign_in_flow::{run_sign_in, SignInEvent};
use super::sign_in_gateway::SignInGateway;

/// The event receiver plus a way to stop the background flow. Respawning
/// the sign-in must abort the previous task: it keeps polling and can still
/// open a browser long after its receiver was replaced.
pub struct SignInHandle {
    rx: mpsc::Receiver<SignInEvent>,
    abort: Option<tokio::task::AbortHandle>,
    /// Carries a guard so the flow's cleanup counts as work until it has sent its cancel.
    cancel: Option<oneshot::Sender<Work>>,
}

impl SignInHandle {
    /// Next event from the sign-in flow, or `None` when it finished.
    pub async fn recv(&mut self) -> Option<SignInEvent> {
        self.rx.recv().await
    }

    /// Whether every event sent so far has been received.
    pub fn is_empty(&self) -> bool {
        self.rx.is_empty()
    }

    /// Stop the background flow.
    pub fn abort(mut self) {
        self.stop();
    }

    pub(super) fn oidc(rx: mpsc::Receiver<SignInEvent>, cancel: oneshot::Sender<Work>) -> Self {
        Self {
            rx,
            abort: None,
            cancel: Some(cancel),
        }
    }

    fn stop(&mut self) {
        if let Some(cancel) = self.cancel.take() {
            let _ = cancel.send(Work::start());
        }
        if let Some(abort) = self.abort.take() {
            abort.abort();
        }
    }
}

impl Drop for SignInHandle {
    fn drop(&mut self) {
        self.stop();
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
    // Busy from the spawn until the flow ends: under replay it is one scripted burst.
    let busy = Work::start();
    // The gateway build can fail (e.g. a system trust store with no CA
    // certificates): fold it into the flow's own Failed event instead of
    // panicking inside the wizard.
    let task = match SignInGateway::new(
        browser_auth_base_url,
        browser_auth_api_base_url,
        allow_origin_rewrite,
        enable_system_trust_store,
    ) {
        Ok(gateway) => crate::work::spawn_daemon(async move {
            if let Err(error) = run_sign_in(gateway, task_tx.clone()).await {
                let _ = task_tx
                    .send(SignInEvent::Failed {
                        message: error.message,
                    })
                    .await;
            }
            drop(busy);
        }),
        Err(error) => crate::work::spawn_daemon(async move {
            let _ = task_tx
                .send(SignInEvent::Failed {
                    message: error.message,
                })
                .await;
            drop(busy);
        }),
    };
    SignInHandle {
        rx,
        abort: Some(task.abort_handle()),
        cancel: None,
    }
}
