//! The background boot behind the drawn frame (ADR 0016's first-draw
//! overlap): initialize and the session handshake in one task owning the
//! ready channel end to end. Only a probe-positive launch runs it — the
//! keyless launch's setup decision lives in the pre-loop wizard round
//! (`preloop`), whose missing-key answer never reaches a handshake.

use std::sync::Arc;

use tokio::sync::mpsc;

use crate::server::Client;
use crate::startup::{HandshakeParams, StartupEvent};

/// Boot the child behind the painted unready frame: `initialize`, then the
/// session handshake on the same child — one task owning the ready channel
/// end to end. The probe (Python `resolve_api_key`: env then keyring)
/// already said the key is present, so no pre-session `setup/status` is
/// asked; a probe-negative launch runs the wizard's pre-loop round instead
/// and hands its booted child back to this handshake's retried spawn.
pub async fn background_boot(
    client: Arc<Client>,
    ready_tx: mpsc::Sender<StartupEvent>,
    trust_rx: mpsc::Receiver<String>,
    mut params: HandshakeParams,
) {
    // A failed initialize is the handshake's failure to surface (its
    // `Failed` event degrades the TUI instead of exiting), so the handshake
    // re-runs it when no version came back.
    params.pre_initialized = crate::startup::initialize_connection(&client).await.ok();
    crate::startup::handshake(client, ready_tx, trust_rx, params).await;
}
