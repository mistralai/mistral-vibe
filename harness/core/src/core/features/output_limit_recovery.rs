//! Recovery from agent completions cut off at the output token limit.
//!
//! A completion that stops at the output limit is unfinished. Without a tool
//! call, accepting it ends the turn on a fragment; with one, the last call's
//! arguments stop mid-value and fail to parse. When recovery is enabled, Core
//! discards the completion, tool calls included, and asks the model to continue
//! with a shorter response, up to a bounded number of times per turn.

use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{AssistantPart, AssistantSemanticPart, CandidateMessage};
use crate::core::wire::tool::echoed_tool_name;

#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Settings {
    /// A completion cut off at the output limit is accepted as it is.
    #[default]
    Disabled,
    /// Discard a cut-off completion and retry with feedback, at most
    /// `max_retries` times per turn.
    Retry { max_retries: u32 },
}

impl Settings {
    pub(crate) fn is_disabled(&self) -> bool {
        matches!(self, Self::Disabled)
    }

    pub(crate) fn validate(self) -> Result<(), CoreError> {
        if matches!(self, Self::Retry { max_retries: 0 }) {
            return Err(CoreError::invalid_configuration(
                "settings.turn.output_limit_recovery.max_retries",
                "output limit recovery max_retries must be greater than zero",
            ));
        }
        Ok(())
    }

    /// Whether a turn that already recovered `retries` times may recover again.
    pub(crate) fn allows_retry(self, retries: u32) -> bool {
        match self {
            Self::Disabled => false,
            Self::Retry { max_retries } => retries < max_retries,
        }
    }
}

const CUT_OFF: &str =
    "Your previous response was discarded because it reached the output token limit";
const AFTER_REASONING: &str = "Use what you already have. Choose one concrete next step, a tool call or your answer, and do not restart the analysis.";
const AFTER_TOOL_CALL: &str = "Split long content or edits into several smaller tool calls.";
const AFTER_ANSWER: &str =
    "Give a shorter answer; put long content in a file with a tool call if needed.";

/// Where a cut-off completion stopped, which decides what the retry asks for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum CutOffAt<'a> {
    /// No tool call and no visible text: the tokens went to reasoning, shown or
    /// hidden.
    Reasoning,
    /// Inside the arguments of the named tool call, the completion's last.
    ToolCall(&'a str),
    /// In the visible answer, without a tool call.
    Answer,
}

impl<'a> CutOffAt<'a> {
    /// Classify a cut-off completion from its parts. Hosts order the parts as
    /// generated, but the classification does not rely on it: any tool call
    /// means the cut came inside one, since a completion's tool calls follow
    /// its text.
    pub(crate) fn of(message: &'a CandidateMessage) -> Self {
        let mut has_text = false;
        let mut last_tool = None;
        for part in &message.content {
            match part {
                AssistantPart::Semantic(AssistantSemanticPart::ToolCall { name, .. }) => {
                    last_tool = Some(name.as_str());
                }
                AssistantPart::Content(ContentBlock::Text(text)) => {
                    has_text |= !text.text.trim().is_empty();
                }
                AssistantPart::Content(_) => has_text = true,
                AssistantPart::Semantic(AssistantSemanticPart::Reasoning { .. }) => {}
            }
        }
        match (last_tool, has_text) {
            (Some(name), _) => Self::ToolCall(name),
            (None, true) => Self::Answer,
            (None, false) => Self::Reasoning,
        }
    }
}

/// The user message that asks the model to try again after a cut-off
/// completion: the output tokens spent, when the provider reported them, and
/// what to do next for the place where the completion stopped.
pub(crate) fn feedback(cut_off_at: CutOffAt<'_>, output_tokens: Option<u64>) -> Vec<ContentBlock> {
    vec![ContentBlock::text(feedback_text(cut_off_at, output_tokens))]
}

fn feedback_text(cut_off_at: CutOffAt<'_>, output_tokens: Option<u64>) -> String {
    let mut text = CUT_OFF.to_string();
    if let Some(tokens) = output_tokens.filter(|tokens| *tokens > 0) {
        text.push_str(&format!(" after {tokens} tokens"));
    }
    text.push_str(". ");
    match cut_off_at {
        CutOffAt::Reasoning => text.push_str(AFTER_REASONING),
        CutOffAt::ToolCall(name) => {
            text.push_str(&format!(
                "It stopped inside the arguments of a `{}` call, so no tool ran. ",
                echoed_tool_name(name)
            ));
            text.push_str(AFTER_TOOL_CALL);
        }
        CutOffAt::Answer => text.push_str(AFTER_ANSWER),
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retry_is_bounded_per_turn() {
        let settings = Settings::Retry { max_retries: 2 };
        assert!(settings.allows_retry(0));
        assert!(settings.allows_retry(1));
        assert!(!settings.allows_retry(2));
        assert!(!Settings::Disabled.allows_retry(0));
    }

    #[test]
    fn zero_retries_are_rejected() {
        assert!(Settings::Retry { max_retries: 0 }.validate().is_err());
        assert!(Settings::Retry { max_retries: 1 }.validate().is_ok());
        assert!(Settings::Disabled.validate().is_ok());
    }

    fn candidate(parts: serde_json::Value) -> CandidateMessage {
        serde_json::from_value(serde_json::json!({"role": "assistant", "content": parts}))
            .expect("candidate message deserializes")
    }

    #[test]
    fn a_cut_in_reasoning_asks_for_one_next_step() {
        let reasoning_only = candidate(serde_json::json!([
            {"type": "reasoning", "content": [{"type": "text", "text": "Let me think about"}]},
            {"type": "text", "text": " "},
        ]));
        let at = CutOffAt::of(&reasoning_only);
        assert_eq!(at, CutOffAt::Reasoning);
        assert_eq!(
            feedback_text(at, Some(32000)),
            "Your previous response was discarded because it reached the output token limit after 32000 tokens. Use what you already have. Choose one concrete next step, a tool call or your answer, and do not restart the analysis."
        );
        assert_eq!(
            CutOffAt::of(&candidate(serde_json::json!([]))),
            CutOffAt::Reasoning
        );
    }

    #[test]
    fn a_cut_in_a_tool_call_names_the_call_and_asks_for_smaller_calls() {
        let cut_in_call = candidate(serde_json::json!([
            {"type": "reasoning", "content": [{"type": "text", "text": "Write it."}]},
            {"type": "text", "text": "Writing the report."},
            {"type": "tool_call", "id": "a", "name": "read_file", "arguments": {"type": "json", "raw": "{}", "value": {}}},
            {"type": "tool_call", "id": "b", "name": "write_file", "arguments": {"type": "invalid_json", "raw": "{\"content\": \"#", "error": "EOF"}},
        ]));
        let at = CutOffAt::of(&cut_in_call);
        assert_eq!(at, CutOffAt::ToolCall("write_file"));
        assert_eq!(
            feedback_text(at, Some(8192)),
            "Your previous response was discarded because it reached the output token limit after 8192 tokens. It stopped inside the arguments of a `write_file` call, so no tool ran. Split long content or edits into several smaller tool calls."
        );
        let long_name = "x".repeat(500);
        let text = feedback_text(CutOffAt::ToolCall(&long_name), None);
        assert!(text.contains(&format!("`{}...`", "x".repeat(64))));
        assert!(!text.contains(&"x".repeat(65)));
    }

    #[test]
    fn a_cut_in_the_answer_asks_for_a_shorter_answer() {
        let cut_in_answer = candidate(serde_json::json!([
            {"type": "reasoning", "content": [{"type": "text", "text": "Answer now."}]},
            {"type": "text", "text": "The report starts with"},
        ]));
        let at = CutOffAt::of(&cut_in_answer);
        assert_eq!(at, CutOffAt::Answer);
        assert_eq!(
            feedback_text(at, None),
            "Your previous response was discarded because it reached the output token limit. Give a shorter answer; put long content in a file with a tool call if needed."
        );
        assert_eq!(feedback_text(at, Some(0)), feedback_text(at, None));
    }

    #[test]
    fn settings_serialize_with_a_mode_tag() {
        let settings: Settings =
            serde_json::from_value(serde_json::json!({"mode": "retry", "max_retries": 6})).unwrap();
        assert_eq!(settings, Settings::Retry { max_retries: 6 });
        assert!(
            serde_json::from_value::<Settings>(
                serde_json::json!({"mode": "retry", "max_retries": 6, "extra": 1})
            )
            .is_err()
        );
    }
}
