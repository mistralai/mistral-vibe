use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ApprovalOutcome {
    Approve,
    Reject,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ApprovalFailureReason {
    Timeout,
}

impl ApprovalFailureReason {
    pub(crate) const fn model_message(self) -> &'static str {
        match self {
            Self::Timeout => "Tool approval timed out.",
        }
    }
}
