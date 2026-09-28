use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::ProtocolError;

/// How a turn ended.
///
/// `Rejected` is distinct from `Completed` because the Core knows why the turn
/// produced no answer, even though the Session Protocol reports both as a
/// completed turn with empty output.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum TurnOutcome {
    Completed { output: Vec<ContentBlock> },
    Rejected { reason: String },
    Failed { error: ProtocolError },
    Interrupted { reason: Option<String> },
}
