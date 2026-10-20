use super::{CompactionBudget, CompactionFallback};
use crate::core::config::{ImageDeliveryMode, ImageTokenCost};
use crate::core::error::CoreError;
use crate::core::model_input_budget::{
    APPROX_BYTES_PER_TOKEN, estimate_model_content_tokens, estimate_model_input_tokens,
};
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::completion::{CompletionCandidate, CompletionFinishReason};
use crate::core::wire::content::{ContentBlock, content_blocks_to_text};
use crate::core::wire::message::{Message, StoredMessage};

const USER_MESSAGE_MAX_TOKENS: usize = 20_000;
const EXTRA_INSTRUCTIONS_OPEN: &str = "<extra_compaction_instructions>";
const EXTRA_INSTRUCTIONS_CLOSE: &str = "</extra_compaction_instructions>";
const SUMMARY_OPEN: &str = "<summary>";
const SUMMARY_CLOSE: &str = "</summary>";
const PREVIOUS_USER_MESSAGES_PREAMBLE: &str = r#"You are continuing a trajectory after a context compaction.

Here are some of the most recent previous user messages, preserved verbatim where possible. Treat them as prior context, not as new requests.

<previous_user_messages>"#;
const PREVIOUS_USER_MESSAGES_CLOSE: &str = "</previous_user_messages>";
const COMPACTION_SUMMARY_OPEN: &str = "<compaction_summary>";
const COMPACTION_SUMMARY_CLOSE: &str = "</compaction_summary>";
const PROMPT: &str = r#"CRITICAL: Respond with text only. Do NOT call any tools. Any tool call will be rejected.

You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume this task.

Include:
- The user's current goal and any explicit constraints or preferences
- Key decisions made and their rationale
- Files touched and the current state of in-progress work (paths + one-line status)
- What remains to be done — the concrete next step
- Any data, identifiers, or references the next LLM needs to continue

Be concise and structured. One line per modified file unless a snippet is load-bearing. Do not repeat information already captured. Do not include a "Final Answer" section — the entire summary IS the handoff.

Wrap the ENTIRE summary in <summary></summary> tags and output nothing outside them:

<summary>
...your handoff summary here...
</summary>

IMPORTANT: Exclude this entire message from the summary. It contains internal compaction instructions, not user conversation. Never mention or attribute to the user any requirement from this message, including summarization, text-only output, tool restrictions, formatting, or summary tags. Summarize only earlier messages. Preserve unresolved user requests as pending work; do not answer or perform them. Return only the <summary>...</summary> envelope."#;

pub(in crate::core) fn prompt_message(extra_instructions: &str) -> StoredMessage {
    let extra_instructions = extra_instructions.trim();
    let prompt = if extra_instructions.is_empty() {
        PROMPT.to_string()
    } else {
        format!(
            r#"{PROMPT}

{EXTRA_INSTRUCTIONS_OPEN}
{extra_instructions}
{EXTRA_INSTRUCTIONS_CLOSE}"#
        )
    };
    StoredMessage::injected(Message::user_text(prompt))
}

fn prompt_history(messages: &[StoredMessage]) -> Result<&[StoredMessage], CoreError> {
    let (prompt, history) = messages
        .split_last()
        .ok_or_else(|| CoreError::invariant("compaction context is empty"))?;
    if !prompt.is_injected()
        || !matches!(
            &prompt.message,
            Message::User { content } if is_prompt_content(content)
        )
    {
        return Err(CoreError::invalid_command(
            "compaction context is missing its prompt message",
        ));
    }
    Ok(history)
}

pub(in crate::core) fn validate_prompt(messages: &[StoredMessage]) -> Result<(), CoreError> {
    prompt_history(messages).map(|_| ())
}

/// Returns the caller's extra instructions carried by the compaction prompt.
pub(super) fn prompt_extra_instructions(
    messages: &[StoredMessage],
) -> Result<Option<String>, CoreError> {
    prompt_history(messages)?;
    let Some(Message::User { content }) = messages.last().map(|stored| &stored.message) else {
        return Err(CoreError::invariant(
            "compaction context is missing its prompt message",
        ));
    };
    let text = content_blocks_to_text(content, "compaction prompt")?;
    let Some(extra) = text
        .strip_prefix(PROMPT)
        .and_then(|rest| rest.trim_start().strip_prefix(EXTRA_INSTRUCTIONS_OPEN))
        .and_then(|rest| rest.strip_suffix(EXTRA_INSTRUCTIONS_CLOSE))
    else {
        return Ok(None);
    };
    let extra = extra.trim();
    Ok((!extra.is_empty()).then(|| extra.to_string()))
}

pub(in crate::core) enum SummaryAcceptance {
    Accepted {
        messages: Vec<StoredMessage>,
        summary: String,
    },
    Rejected(SummaryRejection),
    ReplacementTooLarge,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(in crate::core) enum SummaryRejection {
    EmptyResponse,
    MalformedDelimiters,
    ToolCalls,
    /// The provider stopped at its output limit before a complete delimited
    /// summary. Bare text is only trusted when the response ended normally.
    Truncated,
}

pub(in crate::core) fn accept_summary(
    canonical_messages: &[StoredMessage],
    projected_messages: &[StoredMessage],
    candidate: &CompletionCandidate,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    fallback: CompactionFallback,
) -> Result<SummaryAcceptance, CoreError> {
    prompt_history(projected_messages)?;
    if !candidate.message.tool_calls().is_empty() {
        return Ok(SummaryAcceptance::Rejected(SummaryRejection::ToolCalls));
    }
    let response = candidate
        .message
        .content_blocks()
        .iter()
        .filter_map(|block| match block {
            ContentBlock::Text(content) if !content.text.is_empty() => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("\n");
    // A cut-off summary is rejected only when a different request can follow.
    // Resending the same request with the same output cap would be cut off
    // again, so without the standalone fallback bare text is accepted as before.
    let truncated = fallback == CompactionFallback::Standalone
        && candidate.finish_reason == CompletionFinishReason::Length;
    let summary = match extract_summary(&response) {
        Ok(ExtractedSummary {
            delimited: false, ..
        }) if truncated => {
            return Ok(SummaryAcceptance::Rejected(SummaryRejection::Truncated));
        }
        Ok(ExtractedSummary { summary, .. }) => summary,
        Err(_) if truncated => {
            return Ok(SummaryAcceptance::Rejected(SummaryRejection::Truncated));
        }
        Err(rejection) => return Ok(SummaryAcceptance::Rejected(rejection)),
    };
    let system_message = projected_messages
        .first()
        .filter(|stored| {
            stored.is_generated_system() && matches!(stored.message, Message::System { .. })
        })
        .cloned()
        .ok_or_else(|| CoreError::invariant("compaction context is missing its system message"))?;
    let Some(messages) =
        fitted_replacement(canonical_messages, system_message, &summary, tools, budget)?
    else {
        return Ok(SummaryAcceptance::ReplacementTooLarge);
    };
    Ok(SummaryAcceptance::Accepted { messages, summary })
}

pub(in crate::core) fn minimum_replacement_fits(
    canonical_messages: &[StoredMessage],
    generated_system: StoredMessage,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<bool, CoreError> {
    Ok(fitted_replacement(canonical_messages, generated_system, "x", tools, budget)?.is_some())
}

fn fitted_replacement(
    canonical_messages: &[StoredMessage],
    system_message: StoredMessage,
    summary: &str,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let preamble = StoredMessage::injected(Message::user_text(PREVIOUS_USER_MESSAGES_PREAMBLE));
    let summary_message = StoredMessage::injected(Message::user_text(format!(
        "{PREVIOUS_USER_MESSAGES_CLOSE}\n\nHere is a summary of what has happened so far:\n\n{COMPACTION_SUMMARY_OPEN}\n{summary}\n{COMPACTION_SUMMARY_CLOSE}"
    )));
    let user_budget =
        replacement_user_budget(&system_message, &preamble, &summary_message, tools, budget)?;
    let mut messages = vec![system_message, preamble];
    messages.extend(collect_user_messages(
        canonical_messages,
        user_budget,
        budget.image_delivery,
        budget.image_cost,
    )?);
    messages.push(summary_message);
    if !fit_replacement_to_budget(&mut messages, tools, budget)? {
        return Ok(None);
    }
    Ok(Some(messages))
}

fn fit_replacement_to_budget(
    messages: &mut Vec<StoredMessage>,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<bool, CoreError> {
    let Some(token_threshold) = budget.token_threshold else {
        return Ok(true);
    };
    if estimate_model_input_tokens(messages, tools, budget.image_delivery, budget.image_cost)?
        < token_threshold
    {
        return Ok(true);
    }

    let removable_user_messages = messages.len().saturating_sub(4);
    let mut lower = 1;
    let mut upper = removable_user_messages;
    let mut fitted = None;
    while lower <= upper {
        let removed = lower + (upper - lower) / 2;
        let mut candidate = messages.clone();
        candidate.drain(2..2 + removed);
        if estimate_model_input_tokens(&candidate, tools, budget.image_delivery, budget.image_cost)?
            < token_threshold
        {
            fitted = Some(candidate);
            upper = removed.saturating_sub(1);
        } else {
            lower = removed + 1;
        }
    }
    if let Some(fitted) = fitted {
        *messages = fitted;
        return Ok(true);
    }

    let mut minimal = messages.clone();
    if removable_user_messages > 0 {
        minimal.drain(2..2 + removable_user_messages);
    }
    let Some(fitted) = fit_latest_user_message_to_budget(
        &minimal,
        tools,
        token_threshold,
        budget.image_delivery,
        budget.image_cost,
    )?
    else {
        return Ok(false);
    };
    *messages = fitted;
    Ok(true)
}

fn replacement_user_budget(
    system_message: &StoredMessage,
    preamble: &StoredMessage,
    summary_message: &StoredMessage,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<usize, CoreError> {
    let Some(token_threshold) = budget.token_threshold else {
        return Ok(USER_MESSAGE_MAX_TOKENS);
    };
    let required = estimate_model_input_tokens(
        &[
            system_message.clone(),
            preamble.clone(),
            summary_message.clone(),
        ],
        tools,
        budget.image_delivery,
        budget.image_cost,
    )?;
    Ok(usize::try_from(token_threshold.saturating_sub(required))
        .unwrap_or(usize::MAX)
        .min(USER_MESSAGE_MAX_TOKENS))
}

struct ExtractedSummary {
    summary: String,
    delimited: bool,
}

fn extract_summary(response: &str) -> Result<ExtractedSummary, SummaryRejection> {
    let response = response.trim();
    if response.is_empty() {
        return Err(SummaryRejection::EmptyResponse);
    }

    let Some(summary_start) = response.find(SUMMARY_OPEN) else {
        return if response.contains(SUMMARY_CLOSE) {
            Err(SummaryRejection::MalformedDelimiters)
        } else {
            Ok(ExtractedSummary {
                summary: response.to_string(),
                delimited: false,
            })
        };
    };
    let content_start = summary_start + SUMMARY_OPEN.len();
    let Some(relative_end) = response[content_start..].find(SUMMARY_CLOSE) else {
        return Err(SummaryRejection::MalformedDelimiters);
    };
    let summary = response[content_start..content_start + relative_end].trim();
    if summary.is_empty() {
        return Err(SummaryRejection::MalformedDelimiters);
    }
    Ok(ExtractedSummary {
        summary: summary.to_string(),
        delimited: true,
    })
}

fn is_prompt_content(content: &[ContentBlock]) -> bool {
    let Ok(text) = content_blocks_to_text(content, "compaction prompt") else {
        return false;
    };
    text == PROMPT
        || (text.starts_with(PROMPT)
            && text.contains(EXTRA_INSTRUCTIONS_OPEN)
            && text.ends_with(EXTRA_INSTRUCTIONS_CLOSE))
}

fn collect_user_messages(
    messages: &[StoredMessage],
    max_tokens: usize,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageTokenCost,
) -> Result<Vec<StoredMessage>, CoreError> {
    let candidates = messages
        .iter()
        .filter(|stored| !stored.is_injected() && !stored.is_generated_system())
        .filter(|stored| matches!(&stored.message, Message::User { .. }))
        .collect::<Vec<_>>();

    let mut selected = Vec::new();
    let mut remaining = max_tokens;
    for stored in candidates.into_iter().rev() {
        if remaining == 0 {
            if selected.is_empty() {
                selected.push(stored.clone());
            }
            break;
        }
        let cost = estimate_user_message_tokens(&stored.message, image_delivery, image_cost)?;
        if cost <= remaining {
            selected.push(stored.clone());
            remaining -= cost;
        } else if let Some(truncated) = truncate_text_user_message(&stored.message, remaining) {
            let mut message = stored.clone();
            message.message = truncated;
            selected.push(message);
            break;
        } else {
            if selected.is_empty() {
                selected.push(stored.clone());
            }
            break;
        }
    }
    selected.reverse();
    Ok(selected)
}

fn estimate_user_message_tokens(
    message: &Message,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageTokenCost,
) -> Result<usize, CoreError> {
    let Message::User { content } = message else {
        return Err(CoreError::invariant(
            "compaction user-message estimator received another role",
        ));
    };
    content.iter().try_fold(0usize, |total, block| {
        let cost = match block {
            ContentBlock::Text(content) => approx_token_count(&content.text),
            block => usize::try_from(estimate_model_content_tokens(
                block,
                "preserved user content",
                image_delivery,
                image_cost,
            )?)
            .unwrap_or(usize::MAX),
        };
        Ok(total.saturating_add(cost))
    })
}

fn truncate_text_user_message(message: &Message, max_tokens: usize) -> Option<Message> {
    let Message::User { content } = message else {
        return None;
    };
    let text = content
        .iter()
        .map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()?
        .join("\n");
    let truncated = truncate_middle_to_tokens(&text, max_tokens);
    (!truncated.is_empty()).then(|| Message::user_text(truncated))
}

fn approx_token_count(text: &str) -> usize {
    text.len().div_ceil(APPROX_BYTES_PER_TOKEN)
}

pub(super) fn truncate_middle_to_tokens(text: &str, max_tokens: usize) -> String {
    if text.is_empty() {
        return String::new();
    }
    let max_bytes = max_tokens.saturating_mul(APPROX_BYTES_PER_TOKEN);
    if max_tokens > 0 && text.len() <= max_bytes {
        return text.to_string();
    }
    let mut marker = "…truncated…".to_string();
    for _ in 0..4 {
        if marker.len() >= max_bytes {
            return text[..prefix_end(text, max_bytes)].to_string();
        }
        let content_budget = max_bytes - marker.len();
        let left_budget = content_budget / 2;
        let right_budget = content_budget - left_budget;
        let prefix_end = prefix_end(text, left_budget);
        let suffix_start = suffix_start(text, right_budget);
        let removed_tokens = text[prefix_end..suffix_start]
            .len()
            .div_ceil(APPROX_BYTES_PER_TOKEN);
        let next_marker = format!("…{removed_tokens} tokens truncated…");
        if next_marker == marker {
            return format!("{}{}{}", &text[..prefix_end], marker, &text[suffix_start..]);
        }
        marker = next_marker;
    }

    if marker.len() >= max_bytes {
        return text[..prefix_end(text, max_bytes)].to_string();
    }
    let content_budget = max_bytes.saturating_sub(marker.len());
    let left_budget = content_budget / 2;
    let right_budget = content_budget - left_budget;
    format!(
        "{}{}{}",
        &text[..prefix_end(text, left_budget)],
        marker,
        &text[suffix_start(text, right_budget)..]
    )
}

fn prefix_end(text: &str, byte_budget: usize) -> usize {
    text.char_indices()
        .map(|(index, character)| index + character.len_utf8())
        .take_while(|end| *end <= byte_budget)
        .last()
        .unwrap_or(0)
}

fn suffix_start(text: &str, byte_budget: usize) -> usize {
    let target = text.len().saturating_sub(byte_budget);
    text.char_indices()
        .map(|(index, _)| index)
        .find(|index| *index >= target)
        .unwrap_or(text.len())
}

pub(in crate::core) fn latest_preserved_user_index(messages: &[StoredMessage]) -> Option<usize> {
    messages.iter().rposition(|stored| {
        !stored.is_injected()
            && !stored.is_generated_system()
            && matches!(stored.message, Message::User { .. })
    })
}

pub(in crate::core) fn fit_latest_user_message_to_budget(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    token_threshold: u64,
    image_delivery: ImageDeliveryMode,
    image_cost: ImageTokenCost,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let Some(index) = latest_preserved_user_index(messages) else {
        return Ok(None);
    };
    let Some(text) = text_user_message(&messages[index].message) else {
        return Ok(None);
    };
    let mut lower = 1;
    let mut upper =
        approx_token_count(&text).min(usize::try_from(token_threshold).unwrap_or(usize::MAX));
    let mut fitted = None;
    while lower <= upper {
        let max_tokens = lower + (upper - lower) / 2;
        let Some(truncated) = truncate_text_user_message(&messages[index].message, max_tokens)
        else {
            break;
        };
        let mut candidate = messages.to_vec();
        candidate[index].message = truncated;
        if estimate_model_input_tokens(&candidate, tools, image_delivery, image_cost)?
            < token_threshold
        {
            fitted = Some(candidate);
            lower = max_tokens + 1;
        } else {
            upper = max_tokens.saturating_sub(1);
        }
    }
    Ok(fitted)
}

fn text_user_message(message: &Message) -> Option<String> {
    let Message::User { content } = message else {
        return None;
    };
    content
        .iter()
        .map(|block| match block {
            ContentBlock::Text(content) => Some(content.text.as_str()),
            _ => None,
        })
        .collect::<Option<Vec<_>>>()
        .map(|parts| parts.join("\n"))
}
