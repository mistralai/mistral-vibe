//! `vibeCode/teleport/*` projections from the app server.

use serde::Deserialize;
use serde_json::Value;

/// One `vibeCode/teleport/event` payload (Python `TeleportEvent`).
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
#[serde(
    tag = "kind",
    rename_all = "snake_case",
    rename_all_fields = "camelCase"
)]
pub enum TeleportEvent {
    SummarizingContext,
    CheckingGit,
    PushRequired {
        #[serde(default)]
        unpushed_count: u64,
        #[serde(default)]
        branch_not_pushed: bool,
    },
    Pushing,
    StartingWorkflow,
    Complete {
        url: String,
    },
    Failed {
        error: TeleportError,
    },
}

/// The `PublicError` a failed teleport carries.
#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq)]
pub struct TeleportError {
    pub message: String,
    #[serde(default)]
    pub code: Option<String>,
}

/// Read `(operationId, event)` from a `vibeCode/teleport/event` notification.
pub fn parse_event(params: &Value) -> Option<(String, TeleportEvent)> {
    let event = params.get("event")?;
    let operation_id = event.get("operationId")?.as_str()?.to_owned();
    let event = serde_json::from_value(event.clone()).ok()?;
    Some((operation_id, event))
}
