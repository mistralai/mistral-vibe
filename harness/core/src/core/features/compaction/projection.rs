use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::message::{Message, StoredMessage, tool_calls_from_parts};
use crate::core::wire::tool::ToolCall;

use super::standalone::standalone_messages;
use super::{
    CompactionBudget, CompactionFallback, fit_latest_user_message_to_budget,
    latest_preserved_user_index, prompt_message, validate_prompt,
};

/// An in-context request reaches its second attempt when the Runtime keeps
/// the resend fallback, or in a checkpoint captured by a release that always
/// resent the same request after an invalid summary.
const MAX_IN_CONTEXT_ATTEMPT: u32 = 1;
const MAX_STANDALONE_ATTEMPT: u32 = MAX_IN_CONTEXT_ATTEMPT + 1;

/// The shape of one compaction model request.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompactionRequestKind {
    /// The agent's fitted history and tool catalog followed by the compaction
    /// prompt, sharing its prefix with the agent request.
    #[default]
    InContext,
    /// A dedicated summarizer prompt and a plain-text transcript of the same
    /// fitted history. The Runtime sends it without tools or reasoning.
    Standalone,
}

impl CompactionRequestKind {
    pub(crate) fn is_in_context(&self) -> bool {
        *self == Self::InContext
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CompactionProjection {
    /// The fitted in-context projection. The standalone request derives from
    /// it, so it is the only message list a checkpoint records.
    messages: Vec<StoredMessage>,
    attempt: u32,
    standalone: Option<Vec<StoredMessage>>,
}

/// Whether fitting the request may remove the newest assistant tool calls and
/// their results when no user message follows them.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum NewestToolExchange {
    /// Automatic compaction within a Turn: the agent continues from the
    /// summary, so a summary without those results would make it call the same
    /// tools again.
    Keep,
    /// Manual compaction between Turns: no agent model call follows it in the
    /// Turn, and the next user message starts new work.
    MayRemove,
}

pub(crate) enum CompactionProjectionFit {
    Fitted(CompactionProjection),
    TooLarge,
    /// The request fits only without the newest tool calls and their results,
    /// which the agent has not answered yet. A summary that omits them would
    /// make the agent call the same tools again.
    ToolResultTooLarge {
        tool_names: Vec<String>,
    },
}

impl CompactionProjection {
    pub(crate) fn initial(
        canonical_messages: &[StoredMessage],
        generated_system: StoredMessage,
        extra_instructions: &str,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        newest_tool_exchange: NewestToolExchange,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let mut messages = if canonical_messages.is_empty() {
            vec![generated_system]
        } else {
            canonical_messages.to_vec()
        };
        messages.push(prompt_message(extra_instructions));
        Self::restore(
            messages,
            0,
            CompactionRequestKind::InContext,
            tools,
            budget,
            newest_tool_exchange,
        )
    }

    /// Validates and fits an in-context projection, then derives the
    /// standalone request when `request` asks for one.
    pub(crate) fn restore(
        messages: Vec<StoredMessage>,
        attempt: u32,
        request: CompactionRequestKind,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        newest_tool_exchange: NewestToolExchange,
    ) -> Result<CompactionProjectionFit, CoreError> {
        validate_projection(&messages)?;
        let attempt_in_range = match request {
            CompactionRequestKind::InContext => attempt <= MAX_IN_CONTEXT_ATTEMPT,
            CompactionRequestKind::Standalone => (1..=MAX_STANDALONE_ATTEMPT).contains(&attempt),
        };
        if !attempt_in_range {
            return Err(CoreError::invalid_command(
                "compaction attempt is outside the range of its request kind",
            ));
        }
        let projection = Self {
            messages,
            attempt,
            standalone: None,
        };
        let mut projection = match projection.fit_to_budget(tools, budget, newest_tool_exchange)? {
            CompactionProjectionFit::Fitted(projection) => projection,
            unfitted => return Ok(unfitted),
        };
        if request == CompactionRequestKind::Standalone {
            projection.standalone = Some(standalone_messages(
                &projection.messages,
                budget.token_threshold,
            )?);
        }
        Ok(CompactionProjectionFit::Fitted(projection))
    }

    /// The fitted in-context projection.
    pub(crate) fn messages(&self) -> &[StoredMessage] {
        &self.messages
    }

    /// The messages the Runtime sends for this attempt.
    pub(crate) fn model_messages(&self) -> &[StoredMessage] {
        self.standalone.as_deref().unwrap_or(&self.messages)
    }

    pub(crate) fn attempt(&self) -> u32 {
        self.attempt
    }

    pub(crate) fn request_kind(&self) -> CompactionRequestKind {
        if self.standalone.is_some() {
            CompactionRequestKind::Standalone
        } else {
            CompactionRequestKind::InContext
        }
    }

    /// Returns the request that follows a failed in-context attempt: the
    /// standalone request, or with `Resend` the same in-context request once.
    /// Returns `None` when this attempt was the last one.
    pub(crate) fn fall_back(
        &self,
        fallback: CompactionFallback,
        token_threshold: Option<u64>,
    ) -> Result<Option<Self>, CoreError> {
        if self.standalone.is_some() {
            return Ok(None);
        }
        if fallback == CompactionFallback::Resend && self.attempt >= MAX_IN_CONTEXT_ATTEMPT {
            return Ok(None);
        }
        let attempt = self
            .attempt
            .checked_add(1)
            .ok_or_else(|| CoreError::invariant("compaction request attempt sequence exhausted"))?;
        let standalone = match fallback {
            CompactionFallback::Resend => None,
            CompactionFallback::Standalone => {
                Some(standalone_messages(&self.messages, token_threshold)?)
            }
        };
        Ok(Some(Self {
            messages: self.messages.clone(),
            attempt,
            standalone,
        }))
    }

    fn fit_to_budget(
        mut self,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        newest_tool_exchange: NewestToolExchange,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let Some(token_threshold) = budget.token_threshold else {
            return Ok(CompactionProjectionFit::Fitted(self));
        };
        if estimate_model_input_tokens(
            &self.messages,
            tools,
            budget.image_delivery,
            budget.image_cost,
        )? < token_threshold
        {
            return Ok(CompactionProjectionFit::Fitted(self));
        }

        // Within a Turn, a summary that omits the newest tool results would make
        // the agent call the same tools again, so that exchange is kept while
        // anything else fits.
        let exchange = match newest_tool_exchange {
            NewestToolExchange::Keep => newest_unanswered_tool_exchange(&self.messages),
            NewestToolExchange::MayRemove => None,
        };
        let exchange_start = exchange.as_ref().map(|(start, _)| *start);
        if let Some(fitted) = fit_by_removal(
            &self.messages,
            tools,
            budget,
            token_threshold,
            exchange_start,
        )? {
            self.messages = fitted;
            return Ok(CompactionProjectionFit::Fitted(self));
        }
        if let Some((_, calls)) = exchange
            && fit_by_removal(&self.messages, tools, budget, token_threshold, None)?.is_some()
        {
            return Ok(CompactionProjectionFit::ToolResultTooLarge {
                tool_names: calls.into_iter().map(|call| call.name).collect(),
            });
        }
        Ok(CompactionProjectionFit::TooLarge)
    }
}

/// Removes the fewest oldest history items that bring the request under the
/// threshold, and else truncates the latest user message of the minimal
/// history. Never removes the assistant message at `exchange_start` or the tool
/// results after it.
fn fit_by_removal(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    token_threshold: u64,
    exchange_start: Option<usize>,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let removable_history_items = messages
        .len()
        .saturating_sub(2 + usize::from(latest_preserved_user_index(messages).is_some()));
    let mut lower = 1;
    let mut upper = removable_history_items;
    let mut fitted = None;
    while lower <= upper {
        let removed = lower + (upper - lower) / 2;
        let candidate = remove_oldest_history(messages, removed, exchange_start)?;
        if estimate_model_input_tokens(&candidate, tools, budget.image_delivery, budget.image_cost)?
            < token_threshold
        {
            fitted = Some(candidate);
            upper = removed.saturating_sub(1);
        } else {
            lower = removed + 1;
        }
    }
    if fitted.is_some() {
        return Ok(fitted);
    }
    let minimal = remove_oldest_history(messages, removable_history_items, exchange_start)?;
    fit_latest_user_message_to_budget(
        &minimal,
        tools,
        token_threshold,
        budget.image_delivery,
        budget.image_cost,
    )
}

/// The position and tool calls of the newest assistant message, when it made
/// any and no user message followed it: their results are the newest history
/// items and the agent has not answered them yet. The last projection message
/// is the compaction prompt, so the search starts before it.
fn newest_unanswered_tool_exchange(messages: &[StoredMessage]) -> Option<(usize, Vec<ToolCall>)> {
    let (start, content) =
        messages
            .iter()
            .enumerate()
            .rev()
            .skip(1)
            .find_map(|(index, stored)| match &stored.message {
                Message::Assistant { content } => Some(Some((index, content))),
                Message::User { .. } => Some(None),
                _ => None,
            })??;
    let calls = tool_calls_from_parts(content);
    (!calls.is_empty()).then_some((start, calls))
}

/// Removes the oldest history items one by one. The newest tool exchange,
/// from `exchange_start` on, is kept by position: a provider can reuse a tool
/// call ID in a later turn, so the older messages are paired by ID only among
/// themselves.
fn remove_oldest_history(
    messages: &[StoredMessage],
    removal_count: usize,
    exchange_start: Option<usize>,
) -> Result<Vec<StoredMessage>, CoreError> {
    let (prompt, history) = messages
        .split_last()
        .ok_or_else(|| CoreError::invariant("compaction projection is empty"))?;
    let (older, newest) =
        history.split_at(exchange_start.unwrap_or(history.len()).min(history.len()));
    let mut older = older.to_vec();
    let mut newest = newest.to_vec();
    for _ in 0..removal_count {
        // No user message follows the newest exchange, so the latest user
        // message is among the older messages.
        let protected_user = latest_preserved_user_index(&older);
        if let Some(index) = (1..older.len()).find(|index| Some(*index) != protected_user) {
            let removed = older.remove(index);
            remove_corresponding_tool_message(&mut older, &removed.message);
            continue;
        }
        let Some(index) = newest.iter().position(|stored| {
            !matches!(
                stored.message,
                Message::Assistant { .. } | Message::Tool { .. }
            )
        }) else {
            break;
        };
        newest.remove(index);
    }
    older.append(&mut newest);
    older.push(prompt.clone());
    Ok(older)
}

fn validate_projection(messages: &[StoredMessage]) -> Result<(), CoreError> {
    validate_prompt(messages)?;
    let generated_systems = messages
        .iter()
        .enumerate()
        .filter(|(_, stored)| stored.is_generated_system())
        .collect::<Vec<_>>();
    if !matches!(
        generated_systems.as_slice(),
        [(0, stored)] if matches!(stored.message, Message::System { .. })
    ) {
        return Err(CoreError::invalid_command(
            "compaction projection must begin with one generated system message",
        ));
    }
    Ok(())
}

fn remove_corresponding_tool_message(messages: &mut Vec<StoredMessage>, removed: &Message) {
    match removed {
        Message::Assistant { content } => {
            let call_ids = tool_calls_from_parts(content)
                .into_iter()
                .map(|call| call.id)
                .collect::<Vec<_>>();
            remove_tool_results(messages, &call_ids);
        }
        Message::Tool { tool_call_id, .. } => {
            let Some(assistant_index) = messages.iter().position(|stored| {
                matches!(
                    &stored.message,
                    Message::Assistant { content }
                        if tool_calls_from_parts(content)
                            .iter()
                            .any(|call| call.id == *tool_call_id)
                )
            }) else {
                return;
            };
            let Message::Assistant { content } = &messages[assistant_index].message else {
                return;
            };
            let call_ids = tool_calls_from_parts(content)
                .into_iter()
                .map(|call| call.id)
                .collect::<Vec<_>>();
            messages.remove(assistant_index);
            remove_tool_results(messages, &call_ids);
        }
        Message::System { .. } | Message::User { .. } => {}
    }
}

fn remove_tool_results(messages: &mut Vec<StoredMessage>, call_ids: &[String]) {
    messages.retain(|stored| {
        !matches!(
            &stored.message,
            Message::Tool { tool_call_id, .. }
                if call_ids.iter().any(|call_id| call_id == tool_call_id)
        )
    });
}
