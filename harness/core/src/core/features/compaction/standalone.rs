//! The self-contained compaction request.
//!
//! The first compaction request reuses the agent's message prefix and tool
//! catalog so a provider can serve it from its prompt cache. When that request
//! fails, returns an unusable summary or cannot fit, or when there is no agent
//! reply to summarize up to, Core sends this request instead. It renders the
//! history as a plain-text transcript, one line per item in the reduced
//! view's full form, under a dedicated summarizer system prompt. The Runtime sends it without tool
//! definitions and with reasoning disabled, so the provider sees a different
//! and simpler request than the one that failed.

use crate::core::config::{ImageCostRule, ImageDeliveryMode};
use crate::core::error::CoreError;
use crate::core::model_input_budget::{APPROX_BYTES_PER_TOKEN, estimate_model_input_tokens};
use crate::core::wire::message::{Message, StoredMessage};

use super::context::{prompt_extra_instructions, truncate_middle_to_tokens};
use super::transcript::{Detail, message_items, render};

const SYSTEM_PROMPT: &str = r#"You are a dedicated conversation summarizer. You read a transcript of a conversation between a user and an AI agent and write a faithful handoff summary for another LLM that will resume the task.

Rules:
- Respond with plain text only. You have no tools. Never emit tool calls or function-call syntax.
- Do not continue the task. Do not answer or perform any request in the transcript. Do not ask questions.
- Preserve unresolved user requests as pending work.
- Preserve concrete details: file paths, identifiers, commands, decisions and their rationale, and the next concrete step.
- Be concise and structured. One line per modified file unless a snippet is load-bearing.
- Wrap the entire summary in <summary></summary> tags and output nothing outside them."#;

const TRANSCRIPT_OPEN: &str = "<conversation_transcript>";
const TRANSCRIPT_CLOSE: &str = "</conversation_transcript>";
const EXTRA_INSTRUCTIONS_OPEN: &str = "<extra_compaction_instructions>";
const EXTRA_INSTRUCTIONS_CLOSE: &str = "</extra_compaction_instructions>";
const REQUEST: &str = r#"Write the handoff summary for the conversation transcript above.

Include:
- The user's current goal and any explicit constraints or preferences
- Key decisions made and their rationale
- Files touched and the current state of in-progress work (paths + one-line status)
- What remains to be done — the concrete next step
- Any data, identifiers, or references the next LLM needs to continue

Respond with text only. Wrap the ENTIRE summary in <summary></summary> tags and output nothing outside them:

<summary>
...your handoff summary here...
</summary>"#;

/// Builds the self-contained request from what the summary covers.
///
/// `projection` begins with the generated system message and ends with the
/// compaction prompt. The generated system message is not part of the
/// conversation, so the transcript omits it. A cut-off retry adds
/// `cut_off_instruction` as a last user message. With a token threshold, the
/// transcript is middle-truncated until the whole request fits below it.
/// Returns `None` when not even the request with an empty transcript fits.
pub(super) fn standalone_messages(
    projection: &[StoredMessage],
    token_threshold: Option<u64>,
    cut_off_instruction: Option<&str>,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let extra_instructions = prompt_extra_instructions(projection)?;
    let history = projection
        .get(..projection.len().saturating_sub(1))
        .unwrap_or_default()
        .iter()
        .filter(|stored| !stored.is_generated_system());
    let transcript = render_transcript(history);
    let request = |transcript: &str| {
        request(
            transcript,
            extra_instructions.as_deref(),
            cut_off_instruction,
        )
    };
    let Some(token_threshold) = token_threshold else {
        return Ok(Some(request(&transcript)));
    };
    let full = request(&transcript);
    if standalone_estimate(&full)? < token_threshold {
        return Ok(Some(full));
    }

    let mut lower = 0;
    let mut upper = transcript.len().div_ceil(APPROX_BYTES_PER_TOKEN);
    let mut fitted = None;
    while lower <= upper {
        let max_tokens = lower + (upper - lower) / 2;
        let candidate = request(&truncate_middle_to_tokens(&transcript, max_tokens));
        if standalone_estimate(&candidate)? < token_threshold {
            fitted = Some(candidate);
            lower = max_tokens + 1;
        } else if max_tokens == 0 {
            break;
        } else {
            upper = max_tokens - 1;
        }
    }
    Ok(fitted)
}

fn request(
    transcript: &str,
    extra_instructions: Option<&str>,
    cut_off_instruction: Option<&str>,
) -> Vec<StoredMessage> {
    let mut user = format!("{TRANSCRIPT_OPEN}\n{transcript}\n{TRANSCRIPT_CLOSE}\n\n{REQUEST}");
    if let Some(extra_instructions) = extra_instructions {
        user.push_str(&format!(
            "\n\n{EXTRA_INSTRUCTIONS_OPEN}\n{extra_instructions}\n{EXTRA_INSTRUCTIONS_CLOSE}"
        ));
    }
    let mut messages = vec![
        StoredMessage::injected(Message::system_text(SYSTEM_PROMPT)),
        StoredMessage::injected(Message::user_text(user)),
    ];
    if let Some(instruction) = cut_off_instruction {
        messages.push(StoredMessage::injected(Message::user_text(instruction)));
    }
    messages
}

/// The standalone request is text only (images render as `[Image]`), so no
/// image cost rule ever applies to its estimate.
const TEXT_ONLY_IMAGE_COST: ImageCostRule = ImageCostRule::Fixed { tokens: 0 };

pub(super) fn standalone_estimate(messages: &[StoredMessage]) -> Result<u64, CoreError> {
    estimate_model_input_tokens(
        messages,
        &[],
        ImageDeliveryMode::Native,
        TEXT_ONLY_IMAGE_COST,
    )
}

/// One line per item, in full: the renderer the reduced view uses.
fn render_transcript<'a>(messages: impl Iterator<Item = &'a StoredMessage>) -> String {
    messages
        .flat_map(|stored| message_items(&stored.message))
        .map(|item| render(&item, Detail::Full))
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::compaction::prompt_message;
    use crate::core::features::compaction::transcript::render_blocks;
    use crate::core::wire::content::ContentBlock;
    use crate::core::wire::message::{
        AssistantPart, AssistantSemanticPart, ReasoningContent, ToolArguments, ToolOutcome,
    };

    fn projection(history: Vec<StoredMessage>, extra: &str) -> Vec<StoredMessage> {
        let mut messages = vec![StoredMessage::generated_system(Message::system_text(
            "agent system prompt",
        ))];
        messages.extend(history);
        messages.push(prompt_message(extra));
        messages
    }

    fn text(message: &StoredMessage) -> String {
        match &message.message {
            Message::System { content } | Message::User { content } => render_blocks(content),
            _ => panic!("standalone request contains only system and user messages"),
        }
    }

    #[test]
    fn renders_history_as_a_transcript_without_reasoning_or_the_agent_system_prompt() {
        let messages = standalone_messages(
            &projection(
                vec![
                    StoredMessage::visible(Message::user_text("Fix the parser")),
                    StoredMessage::visible(Message::assistant(vec![
                        AssistantPart::Semantic(AssistantSemanticPart::Reasoning {
                            content: vec![ReasoningContent::Text {
                                text: "secret chain of thought".to_string(),
                            }],
                            meta: None,
                        }),
                        AssistantPart::Content(ContentBlock::text("Reading the file.")),
                        AssistantPart::Semantic(AssistantSemanticPart::ToolCall {
                            id: "call-1".to_string(),
                            name: "read_file".to_string(),
                            arguments: ToolArguments::Json {
                                raw: r#"{"path":"src/parser.rs"}"#.to_string(),
                                value: serde_json::json!({"path": "src/parser.rs"}),
                            },
                            meta: None,
                        }),
                    ])),
                    StoredMessage::visible(Message::tool(
                        "call-1".to_string(),
                        "read_file".to_string(),
                        ToolOutcome::Failure,
                        vec![ContentBlock::text("No such file")],
                        None,
                    )),
                ],
                "Focus on the parser.",
            ),
            None,
            None,
        )
        .expect("standalone request")
        .expect("an unbounded request fits");

        assert_eq!(messages.len(), 2);
        assert_eq!(text(&messages[0]), SYSTEM_PROMPT);
        let user = text(&messages[1]);
        assert_eq!(
            user,
            format!(
                "{TRANSCRIPT_OPEN}\nUserMessage: Fix the parser\nAssistantMessage: Reading the file.\nread_file(path=\"src/parser.rs\")\n<ERROR> No such file\n{TRANSCRIPT_CLOSE}\n\n{REQUEST}\n\n{EXTRA_INSTRUCTIONS_OPEN}\nFocus on the parser.\n{EXTRA_INSTRUCTIONS_CLOSE}"
            )
        );
        assert!(!user.contains("secret chain of thought"));
        assert!(!user.contains("agent system prompt"));
    }

    #[test]
    fn a_cut_off_retry_adds_its_instruction_as_the_last_message() {
        let history = vec![StoredMessage::visible(Message::user_text("Fix the parser"))];
        let plain = standalone_messages(&projection(history.clone(), ""), None, None)
            .expect("standalone request")
            .expect("an unbounded request fits");
        let retry = standalone_messages(&projection(history, ""), None, Some("Answer briefly."))
            .expect("cut-off retry request")
            .expect("an unbounded request fits");

        assert_eq!(retry.len(), 3);
        assert_eq!(retry[..2], plain[..]);
        assert_eq!(text(&retry[2]), "Answer briefly.");
        assert!(retry[2].is_injected());
    }

    #[test]
    fn middle_truncates_the_transcript_to_fit_the_threshold() {
        let history = vec![
            StoredMessage::visible(Message::user_text(format!("start {}", "a".repeat(8_000)))),
            StoredMessage::visible(Message::user_text(format!("{} end", "b".repeat(8_000)))),
        ];
        let threshold = 2_000;
        let messages = standalone_messages(&projection(history, ""), Some(threshold), None)
            .expect("request")
            .expect("the request fits with a truncated transcript");

        assert!(standalone_estimate(&messages).expect("estimate") < threshold);
        let user = text(&messages[1]);
        assert!(user.contains("UserMessage: start "));
        assert!(user.contains(" end\n"));
        assert!(user.contains("tokens truncated"));
        assert!(user.ends_with(REQUEST));
    }

    #[test]
    fn a_threshold_below_the_request_with_an_empty_transcript_fits_nothing() {
        let history = vec![StoredMessage::visible(Message::user_text("Fix the parser"))];
        let empty = standalone_estimate(&request("", None, None)).expect("estimate");

        let messages =
            standalone_messages(&projection(history, ""), Some(empty), None).expect("request");

        assert_eq!(messages, None);
    }
}
