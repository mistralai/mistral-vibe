//! Where a `callback/call` is answered, and the `callback/result` outputs.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use super::{ApprovalDecisionType, UserQuestionResult};

/// The callback to answer; `session_id` is missing when an older server omits it (ADR 0014).
#[derive(Debug, Clone, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CallbackAddress {
    #[serde(default)]
    pub callback_id: String,
    #[serde(default)]
    pub session_id: Option<String>,
}

impl CallbackAddress {
    /// The address of a `callback/call`'s `callback` object.
    pub fn parse(callback: &Value) -> Option<Self> {
        Self::deserialize(callback)
            .ok()
            .filter(|address| !address.callback_id.is_empty())
    }

    /// The `callback/result` params; `fallback_session` is used only when the server omitted the session.
    pub fn result_params(
        &self,
        fallback_session: Option<&str>,
        output: &CallbackOutput,
    ) -> Option<Value> {
        let session_id = self
            .session_id
            .as_deref()
            .filter(|id| !id.is_empty())
            .or(fallback_session)
            .filter(|id| !id.is_empty())?;
        Some(json!({
            "sessionId": session_id,
            "result": {"callbackId": self.callback_id, "output": output},
        }))
    }
}

/// A `callback/result` output, one variant per advertised callback kind.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum CallbackOutput {
    Approval {
        decision: ApprovalDecision,
        feedback: Option<String>,
    },
    UserInput {
        result: UserQuestionResult,
    },
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct ApprovalDecision {
    #[serde(rename = "type")]
    pub kind: ApprovalDecisionType,
}

impl CallbackOutput {
    pub fn approval(decision: ApprovalDecisionType) -> Self {
        Self::Approval {
            decision: ApprovalDecision { kind: decision },
            feedback: None,
        }
    }

    /// The headless answer to a callback of `kind`, or `None` for an unadvertised kind.
    pub fn denial(kind: &str) -> Option<Self> {
        match kind {
            "approval" => Some(Self::approval(ApprovalDecisionType::Deny)),
            "user_input" => Some(Self::UserInput {
                result: UserQuestionResult {
                    answers: Vec::new(),
                    cancelled: true,
                },
            }),
            _ => None,
        }
    }
}
