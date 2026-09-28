//! Workspace-trust resolution, before anything session-related runs.

use std::sync::Arc;

use anyhow::{Context, Result};
use tokio::sync::mpsc;

use crate::server::{
    method, Client, WorkspaceTrustDecisionParams, WorkspaceTrustStatusParams,
    WorkspaceTrustStatusResponse,
};

use super::StartupEvent;

/// Gate the session on the user's answer when the cwd is untrusted (Python
/// `_resolve_workspace_trust`). A trusted cwd answers without any details.
pub async fn resolve_workspace_trust(
    client: &Arc<Client>,
    event_tx: &mpsc::Sender<StartupEvent>,
    cwd: &Option<String>,
    trust_rx: &mut mpsc::Receiver<String>,
) -> Result<()> {
    let params = WorkspaceTrustStatusParams { cwd: cwd.clone() };
    let value = serde_json::to_value(&params)?;
    let result = client
        .request(method::WORKSPACE_TRUST_STATUS, value)
        .await
        .context("workspace/trust/status")?;
    let status: WorkspaceTrustStatusResponse =
        serde_json::from_value(result).context("workspace/trust/status response")?;
    let Some(details) = status.details else {
        return Ok(());
    };
    let decision_cwd = Some(details.cwd.clone());
    event_tx
        .send(StartupEvent::Trust(Box::new(details)))
        .await
        .context("startup event receiver closed")?;
    let decision = trust_rx.recv().await.context("trust gate closed")?;
    let params = WorkspaceTrustDecisionParams {
        decision,
        cwd: decision_cwd,
        session_id: None,
    };
    let value = serde_json::to_value(&params)?;
    client
        .request(method::WORKSPACE_TRUST_DECISION, value)
        .await
        .context("workspace/trust/decision")?;
    Ok(())
}
