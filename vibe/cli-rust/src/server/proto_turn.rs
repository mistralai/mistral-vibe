//! App-server `PublicTurn` and `PublicTurnQueue`; every field defaults (ADR 0014).

use serde::Deserialize;
use serde_json::Value;

use super::ContentBlock;

#[derive(Default, Deserialize)]
#[serde(default)]
struct TurnParams {
    turn: PublicTurn,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct PublicTurn {
    pub id: Option<String>,
    pub status: Option<String>,
    pub queue_item_id: Option<String>,
    pub stop_reason: Option<String>,
    #[serde(deserialize_with = "crate::utils::json::null_default")]
    pub error: TurnError,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct TurnError {
    pub code: Option<String>,
    pub message: Option<String>,
}

impl PublicTurn {
    /// The `turn` of a notification's params.
    pub fn read(params: &Value) -> Self {
        crate::utils::json::read::<TurnParams>(params).turn
    }

    pub fn status(&self) -> Option<&str> {
        self.status.as_deref()
    }

    pub fn error_code(&self) -> Option<&str> {
        self.error.code.as_deref()
    }

    pub fn error_message(&self) -> Option<&str> {
        self.error.message.as_deref()
    }

    /// The error code of a failed turn.
    pub fn failure(&self) -> Option<&str> {
        self.error_code()
            .filter(|_| self.status() == Some("failed"))
    }
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default)]
pub struct PublicTurnQueue {
    pub paused: bool,
    #[serde(deserialize_with = "crate::utils::json::lenient")]
    pub items: Vec<QueuedTurn>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct QueuedTurn {
    pub id: String,
    #[serde(default, deserialize_with = "crate::utils::json::lenient")]
    pub entries: Vec<QueuedEntry>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct QueuedEntry {
    pub entry_id: Option<String>,
    pub role: Option<String>,
    #[serde(deserialize_with = "crate::utils::json::lenient")]
    pub content: Vec<ContentBlock>,
}
