use serde::{Deserialize, Serialize};

use crate::core::error::CoreError;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{
    AssistantPart, Message, StoredMessage, ToolOutcome, tool_calls_from_parts,
};

use super::standalone::{standalone_estimate, standalone_messages};
use super::{
    CompactionBudget, Prompts, fit_latest_user_message_to_budget, latest_preserved_user_index,
    prompt_message, validate_prompt,
};

/// The shape of one compaction model request, as the Runtime sees it.
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

/// How many times a cut-off summary is asked for again before Core keeps
/// what it has.
pub(crate) const SUMMARY_CUT_OFF_RETRIES: u32 = 1;

/// Which summary request a compaction has out. A checkpoint records it while
/// the request is out.
///
/// ```text
/// (no reply yet) ----------------------------+
///                                            v
/// InContext --failed or minimal--> Standalone --failed--> Failed
///     |                                 |
///     +------------cut off--------------+--> CutOffRetry --cut off or failed--> Truncate
///                                                                                  (Failed without a partial)
/// ```
///
/// A refusal as too long refits the request within the same phase. A
/// complete summary from any phase goes on to replace the history. Truncate
/// keeps the longest cut-off text, cut at a line end to the room and noted,
/// in place of a summary.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum CompactionPhase {
    /// The last accepted agent request, its reply, a placeholder result per
    /// tool call of the reply, then the compaction prompt. The provider can
    /// serve its prefix from the cache of the agent request.
    InContext,
    /// The summarizer prompt and a text transcript of the same history.
    Standalone,
    /// The standalone request again after a cut-off summary, with the
    /// instruction to answer briefly. `partial` is the longest cut-off text
    /// so far; an empty one is `None`.
    CutOffRetry {
        attempt: u32,
        partial: Option<String>,
    },
}

impl CompactionPhase {
    pub(crate) fn request_kind(&self) -> CompactionRequestKind {
        match self {
            Self::InContext => CompactionRequestKind::InContext,
            Self::Standalone | Self::CutOffRetry { .. } => CompactionRequestKind::Standalone,
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CompactionProjection {
    /// What the summary covers: the history up to and including the last
    /// reply, a placeholder result per tool call of the reply, then the
    /// compaction prompt. Without a reply, the whole history and the prompt.
    /// Every phase's request derives from it, so it is the only message list
    /// a checkpoint records.
    messages: Vec<StoredMessage>,
    /// The number of summary requests sent before this one in this compaction.
    attempt: u32,
    phase: CompactionPhase,
    /// After a refusal as too long in this compaction, the estimate every
    /// later request stays under. See [`after_refusal`](Self::after_refusal).
    refit_limit: Option<u64>,
    /// The messages this attempt sends.
    request: Vec<StoredMessage>,
}

pub(crate) enum CompactionProjectionFit {
    Fitted(CompactionProjection),
    /// Not even the smallest standalone request fits the budget.
    TooLarge,
}

impl CompactionProjection {
    /// The first summary request of a compaction: in context when the history
    /// has a reply, else standalone.
    pub(crate) fn initial(
        canonical_messages: &[StoredMessage],
        generated_system: StoredMessage,
        extra_instructions: &str,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let messages = summary_scope(
            canonical_messages,
            generated_system,
            extra_instructions,
            prompts,
        );
        validate_projection(&messages)?;
        let phase = if reply_index(&messages).is_some() {
            CompactionPhase::InContext
        } else {
            CompactionPhase::Standalone
        };
        Self::fit(messages, 0, phase, None, tools, budget, prompts)
    }

    /// Validates a projection a checkpoint recorded and fits the request its
    /// phase sends.
    pub(crate) fn restore(
        messages: Vec<StoredMessage>,
        attempt: u32,
        phase: CompactionPhase,
        refit_limit: Option<u64>,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<CompactionProjectionFit, CoreError> {
        validate_projection(&messages)?;
        validate_phase(&phase, attempt)?;
        Self::fit(
            messages,
            attempt,
            phase,
            refit_limit,
            tools,
            budget,
            prompts,
        )
    }

    /// What the summary covers, ending with the compaction prompt.
    pub(crate) fn messages(&self) -> &[StoredMessage] {
        &self.messages
    }

    /// The messages the Runtime sends for this attempt.
    pub(crate) fn model_messages(&self) -> &[StoredMessage] {
        &self.request
    }

    pub(crate) fn attempt(&self) -> u32 {
        self.attempt
    }

    pub(crate) fn phase(&self) -> &CompactionPhase {
        &self.phase
    }

    pub(crate) fn refit_limit(&self) -> Option<u64> {
        self.refit_limit
    }

    pub(crate) fn request_kind(&self) -> CompactionRequestKind {
        self.phase.request_kind()
    }

    /// The uncorrected estimate of this attempt's request, as the Runtime
    /// sends it: with the tool catalog in context, without it standalone.
    pub(crate) fn request_estimate(
        &self,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
    ) -> Result<u64, CoreError> {
        estimate_request(&self.request, &self.phase, tools, budget)
    }

    /// The request that follows a failed attempt: Standalone after
    /// InContext. Returns `None` when nothing follows and the compaction
    /// fails.
    pub(crate) fn after_failure(
        &self,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<Option<Self>, CoreError> {
        match self.phase {
            CompactionPhase::InContext => {
                self.next_attempt(CompactionPhase::Standalone, None, tools, budget, prompts)
            }
            CompactionPhase::Standalone | CompactionPhase::CutOffRetry { .. } => Ok(None),
        }
    }

    /// The request that follows a cut-off summary whose text so far is
    /// `partial`: a cut-off retry while retries are left, keeping the longer
    /// partial. Returns `None` when no retry is left: the compaction then
    /// keeps the longer partial (see [`Self::kept_partial`]).
    pub(crate) fn after_cut_off(
        &self,
        partial: Option<String>,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<Option<Self>, CoreError> {
        let partial = partial.filter(|partial| !partial.trim().is_empty());
        let (attempt, partial) = match &self.phase {
            CompactionPhase::InContext | CompactionPhase::Standalone => (1, partial),
            CompactionPhase::CutOffRetry {
                attempt,
                partial: saved,
            } => {
                if *attempt >= SUMMARY_CUT_OFF_RETRIES {
                    return Ok(None);
                }
                (attempt + 1, longer(saved.clone(), partial))
            }
        };
        self.next_attempt(
            CompactionPhase::CutOffRetry { attempt, partial },
            None,
            tools,
            budget,
            prompts,
        )
    }

    /// The cut-off text kept when nothing follows this attempt: the longer
    /// of the partial saved by a cut-off retry and `partial`, the text of
    /// this attempt's own cut-off response. `None` when both are empty.
    pub(crate) fn kept_partial(&self, partial: Option<String>) -> Option<String> {
        let partial = partial.filter(|partial| !partial.trim().is_empty());
        match &self.phase {
            CompactionPhase::CutOffRetry { partial: saved, .. } => longer(saved.clone(), partial),
            CompactionPhase::InContext | CompactionPhase::Standalone => partial,
        }
    }

    /// The request that follows a refusal as too long of this attempt's
    /// request: the same phase, refitted under both the budget, counted at
    /// the grown correction, and `refit_limit` (see
    /// [`EstimateCorrection::refit_limit`](super::overflow::EstimateCorrection::refit_limit)). The refit is therefore smaller by
    /// at least the correction's floor growth even when the budget is far
    /// above the refused request. An in-context request already at its minimal size
    /// goes to Standalone. Returns `None` when even the minimal request of
    /// the last phase does not fit.
    pub(crate) fn after_refusal(
        &self,
        refit_limit: u64,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<Option<Self>, CoreError> {
        let refit_limit = self
            .refit_limit
            .map_or(refit_limit, |saved| saved.min(refit_limit));
        self.next_attempt(
            self.phase.clone(),
            Some(refit_limit),
            tools,
            budget,
            prompts,
        )
    }

    fn next_attempt(
        &self,
        phase: CompactionPhase,
        refit_limit: Option<u64>,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<Option<Self>, CoreError> {
        let attempt = self
            .attempt
            .checked_add(1)
            .ok_or_else(|| CoreError::invariant("compaction request attempt sequence exhausted"))?;
        match Self::fit(
            self.messages.clone(),
            attempt,
            phase,
            refit_limit.or(self.refit_limit),
            tools,
            budget,
            prompts,
        )? {
            CompactionProjectionFit::Fitted(next) => Ok(Some(next)),
            CompactionProjectionFit::TooLarge => Ok(None),
        }
    }

    /// Fits the request `phase` sends under the budget and below every
    /// refused estimate. An in-context request that does not fit even with
    /// the oldest history removed becomes a standalone one.
    fn fit(
        messages: Vec<StoredMessage>,
        attempt: u32,
        phase: CompactionPhase,
        refit_limit: Option<u64>,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
        prompts: &Prompts,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let limit = request_limit(budget.token_threshold, refit_limit);
        let mut phase = phase;
        if phase == CompactionPhase::InContext {
            if let Some(request) = fit_in_context(&messages, tools, budget, limit)? {
                return Ok(CompactionProjectionFit::Fitted(Self {
                    messages,
                    attempt,
                    phase,
                    refit_limit,
                    request,
                }));
            }
            phase = CompactionPhase::Standalone;
        }
        let cut_off_instruction = match &phase {
            CompactionPhase::CutOffRetry { .. } => Some(prompts.cut_off_retry.as_str()),
            CompactionPhase::InContext | CompactionPhase::Standalone => None,
        };
        let Some(request) = standalone_messages(&messages, limit, cut_off_instruction)? else {
            return Ok(CompactionProjectionFit::TooLarge);
        };
        Ok(CompactionProjectionFit::Fitted(Self {
            messages,
            attempt,
            phase,
            refit_limit,
            request,
        }))
    }
}

/// The history a summary covers: up to and including the last reply, then a
/// placeholder result per tool call of the reply, then the compaction prompt.
/// The prefix is the last agent request the provider accepted, byte for byte,
/// so the provider can serve it from its cache. The reply's real results and
/// anything newer go back after the summary when the history is rebuilt.
fn summary_scope(
    canonical_messages: &[StoredMessage],
    generated_system: StoredMessage,
    extra_instructions: &str,
    prompts: &Prompts,
) -> Vec<StoredMessage> {
    let mut messages = if canonical_messages.is_empty() {
        vec![generated_system]
    } else {
        canonical_messages.to_vec()
    };
    if let Some(reply) = messages
        .iter()
        .rposition(|stored| matches!(stored.message, Message::Assistant { .. }))
    {
        messages.truncate(reply + 1);
        let Message::Assistant { content } = &messages[reply].message else {
            unreachable!("the reply is an assistant message");
        };
        let placeholders = tool_calls_from_parts(content)
            .into_iter()
            .map(|call| {
                StoredMessage::injected(Message::tool(
                    call.id,
                    call.name,
                    ToolOutcome::Success,
                    vec![ContentBlock::text(prompts.tool_result_placeholder.clone())],
                    None,
                ))
            })
            .collect::<Vec<_>>();
        messages.extend(placeholders);
    }
    messages.push(prompt_message(extra_instructions));
    messages
}

/// The position of the reply the summary scope ends with: the last assistant
/// message, followed only by its placeholders and the prompt.
fn reply_index(messages: &[StoredMessage]) -> Option<usize> {
    messages
        .iter()
        .rposition(|stored| matches!(stored.message, Message::Assistant { .. }))
}

/// The estimate a request must stay under: the budget, and the refit limit
/// after a refusal.
fn request_limit(token_threshold: Option<u64>, refit_limit: Option<u64>) -> Option<u64> {
    match (token_threshold, refit_limit) {
        (Some(threshold), Some(refit)) => Some(threshold.min(refit)),
        (threshold, None) => threshold,
        (None, refit) => refit,
    }
}

fn estimate_request(
    request: &[StoredMessage],
    phase: &CompactionPhase,
    tools: &[ToolDefinition],
    budget: CompactionBudget,
) -> Result<u64, CoreError> {
    match phase {
        CompactionPhase::InContext => {
            estimate_model_input_tokens(request, tools, budget.image_delivery, budget.image_cost)
        }
        CompactionPhase::Standalone | CompactionPhase::CutOffRetry { .. } => {
            standalone_estimate(request)
        }
    }
}

/// The in-context request under `limit`, with the fewest oldest history
/// items removed. The reply and its placeholders are never removed. `None`
/// when even the minimal request does not fit.
fn fit_in_context(
    messages: &[StoredMessage],
    tools: &[ToolDefinition],
    budget: CompactionBudget,
    limit: Option<u64>,
) -> Result<Option<Vec<StoredMessage>>, CoreError> {
    let Some(limit) = limit else {
        return Ok(Some(messages.to_vec()));
    };
    if estimate_model_input_tokens(messages, tools, budget.image_delivery, budget.image_cost)?
        < limit
    {
        return Ok(Some(messages.to_vec()));
    }
    fit_by_removal(messages, tools, budget, limit, reply_index(messages))
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

/// Removes the oldest history items one by one. The newest tool exchange,
/// from `exchange_start` on, is kept by position: a provider can reuse a tool
/// call ID in a later turn, so a removed tool call takes with it only the
/// results of its own exchange.
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
            remove_rest_of_exchange(&mut older, index, &removed.message);
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

/// The longer of two partial summaries.
fn longer(saved: Option<String>, partial: Option<String>) -> Option<String> {
    match (saved, partial) {
        (Some(saved), Some(partial)) if partial.len() > saved.len() => Some(partial),
        (Some(saved), _) => Some(saved),
        (None, partial) => partial,
    }
}

/// A cut-off retry follows at least one cut-off request and is within the
/// retries allowed. A refusal as too long refits within a phase, so the other
/// phases can be at any attempt.
fn validate_phase(phase: &CompactionPhase, attempt: u32) -> Result<(), CoreError> {
    let CompactionPhase::CutOffRetry { attempt: retry, .. } = phase else {
        return Ok(());
    };
    if attempt == 0 || !(1..=SUMMARY_CUT_OFF_RETRIES).contains(retry) {
        return Err(CoreError::invalid_command(format!(
            "compaction attempt {attempt} with cut-off retry {retry} is outside the range of \
             the cut-off retry phase"
        )));
    }
    Ok(())
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

/// Removes the rest of the tool exchange that the message removed at `index`
/// belonged to. A provider can reuse a tool call ID in a later turn, so the
/// exchange is found by position: the assistant message precedes its results,
/// and a later assistant message that reuses an ID starts another exchange.
fn remove_rest_of_exchange(messages: &mut Vec<StoredMessage>, index: usize, removed: &Message) {
    match removed {
        Message::Assistant { content } => {
            remove_tool_results_from(messages, index, tool_call_ids(content));
        }
        Message::Tool { tool_call_id, .. } => {
            let Some(assistant_index) = messages[..index].iter().rposition(|stored| {
                matches!(
                    &stored.message,
                    Message::Assistant { content }
                        if tool_call_ids(content).contains(tool_call_id)
                )
            }) else {
                return;
            };
            let removed = messages.remove(assistant_index);
            let Message::Assistant { content } = &removed.message else {
                return;
            };
            remove_tool_results_from(messages, assistant_index, tool_call_ids(content));
        }
        Message::System { .. } | Message::User { .. } => {}
    }
}

fn tool_call_ids(content: &[AssistantPart]) -> Vec<String> {
    tool_calls_from_parts(content)
        .into_iter()
        .map(|call| call.id)
        .collect()
}

/// Removes the results of `call_ids` from `start` on. An ID stops matching at
/// the next assistant message that reuses it.
fn remove_tool_results_from(
    messages: &mut Vec<StoredMessage>,
    start: usize,
    mut call_ids: Vec<String>,
) {
    let mut index = start;
    while index < messages.len() && !call_ids.is_empty() {
        match &messages[index].message {
            Message::Assistant { content } => {
                let reused = tool_call_ids(content);
                call_ids.retain(|call_id| !reused.contains(call_id));
            }
            Message::Tool { tool_call_id, .. } if call_ids.contains(tool_call_id) => {
                messages.remove(index);
                continue;
            }
            _ => {}
        }
        index += 1;
    }
}

#[cfg(test)]
mod tests;
