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
const CONTINUE: &str = "Continue the task with a shorter response: keep reasoning brief, and split long file contents or edits into several smaller tool calls.";

/// The user message that asks the model to try again after a cut-off
/// completion. It names the tool call that was cut off, if any, and the output
/// tokens spent, when the provider reported them.
pub(crate) fn feedback(
    cut_off_tool: Option<&str>,
    output_tokens: Option<u64>,
) -> Vec<ContentBlock> {
    vec![ContentBlock::text(feedback_text(
        cut_off_tool,
        output_tokens,
    ))]
}

fn feedback_text(cut_off_tool: Option<&str>, output_tokens: Option<u64>) -> String {
    let mut text = CUT_OFF.to_string();
    if let Some(tokens) = output_tokens.filter(|tokens| *tokens > 0) {
        text.push_str(&format!(" after {tokens} tokens"));
    }
    text.push_str(" before it finished.");
    if let Some(name) = cut_off_tool {
        text.push_str(&format!(
            " It was cut off while writing the arguments of a `{}` call, so none of its tool calls ran.",
            echoed_tool_name(name)
        ));
    }
    text.push(' ');
    text.push_str(CONTINUE);
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

    #[test]
    fn feedback_without_details_keeps_the_plain_message() {
        assert_eq!(
            feedback_text(None, None),
            "Your previous response was discarded because it reached the output token limit before it finished. Continue the task with a shorter response: keep reasoning brief, and split long file contents or edits into several smaller tool calls."
        );
        assert_eq!(feedback_text(None, Some(0)), feedback_text(None, None));
    }

    #[test]
    fn feedback_names_the_cut_off_tool_call_and_the_tokens_spent() {
        let text = feedback_text(Some("write_file"), Some(8192));
        assert!(text.starts_with(
            "Your previous response was discarded because it reached the output token limit after 8192 tokens before it finished. It was cut off while writing the arguments of a `write_file` call, so none of its tool calls ran."
        ));
        let long_name = "x".repeat(500);
        let text = feedback_text(Some(&long_name), None);
        assert!(text.contains(&format!("`{}...`", "x".repeat(64))));
        assert!(!text.contains(&"x".repeat(65)));
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
