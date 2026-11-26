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
    /// The text the model receives. A timeout means that nobody answered, so
    /// the model must not ask again at once: each new call opens a new
    /// approval that waits for the full timeout again.
    pub(crate) const fn model_message(self) -> &'static str {
        match self {
            Self::Timeout => {
                "Tool approval timed out: nobody approved or rejected this call in time, so it did not run. Treat it as rejected. Do not retry the same call right away."
            }
        }
    }
}
