//! Wire `ContextBreakdown` (app-server `protocol.py`); every field defaults (ADR 0014).

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ContextCategoryId {
    System,
    Tools,
    Skills,
    Conversation,
    /// A category a newer server added; rendered as "Other" (ADR 0014).
    #[serde(other)]
    Unknown,
}

impl ContextCategoryId {
    pub fn label(self) -> &'static str {
        match self {
            Self::System => "System prompt",
            Self::Tools => "Tool definitions",
            Self::Skills => "Skills",
            Self::Conversation => "Conversation",
            Self::Unknown => "Other",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct ContextCategory {
    pub id: Option<ContextCategoryId>,
    pub characters: u64,
    pub estimated_tokens: u64,
}

#[derive(Debug, Clone, Default, PartialEq, Deserialize)]
#[serde(default, rename_all = "camelCase")]
pub struct ContextBreakdown {
    pub context_window: u64,
    pub compaction_threshold: u64,
    pub used_tokens: u64,
    pub measured: bool,
    pub total_characters: u64,
    pub categories: Vec<ContextCategory>,
}
