//! Wire types for `session/fork` (app-server `SessionForkParams`/`SessionForkResponse`).

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionForkParams {
    pub source_session_id: String,
    /// `None` forks the whole conversation.
    pub entry_id: Option<String>,
    /// `false` leaves the current session attached and the copy on disk.
    pub attach: bool,
}

/// Only the forked session's identity is read; the rest of the state is ignored.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionForkResponse {
    pub state: ForkedState,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ForkedState {
    pub session: ForkedSession,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ForkedSession {
    pub id: String,
    /// `"legacy"` or `"unified"`; absent on older servers.
    #[serde(default)]
    pub harness: Option<String>,
}
