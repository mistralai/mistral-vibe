use crate::core::error::CoreError;
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::step_protocol::ToolDefinition;
use crate::core::wire::message::{Message, StoredMessage, tool_calls_from_parts};

use super::{
    CompactionBudget, fit_latest_user_message_to_budget, latest_preserved_user_index,
    prompt_message, validate_prompt,
};

const MAX_SUMMARY_RETRIES: u32 = 1;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct CompactionProjection {
    messages: Vec<StoredMessage>,
    attempt: u32,
}

pub(crate) enum CompactionProjectionFit {
    Fitted(CompactionProjection),
    TooLarge,
}

impl CompactionProjection {
    pub(crate) fn initial(
        canonical_messages: &[StoredMessage],
        generated_system: StoredMessage,
        extra_instructions: &str,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let mut messages = if canonical_messages.is_empty() {
            vec![generated_system]
        } else {
            canonical_messages.to_vec()
        };
        messages.push(prompt_message(extra_instructions));
        Self::restore(messages, 0)?.fit_to_budget(tools, budget)
    }

    pub(crate) fn restore(messages: Vec<StoredMessage>, attempt: u32) -> Result<Self, CoreError> {
        validate_projection(&messages)?;
        if attempt > MAX_SUMMARY_RETRIES {
            return Err(CoreError::invalid_command(
                "compaction summary retry attempt exceeds its limit",
            ));
        }
        Ok(Self { messages, attempt })
    }

    pub(crate) fn messages(&self) -> &[StoredMessage] {
        &self.messages
    }

    pub(crate) fn attempt(&self) -> u32 {
        self.attempt
    }

    pub(crate) fn retry_after_invalid_summary(&self) -> Result<Option<Self>, CoreError> {
        if self.attempt >= MAX_SUMMARY_RETRIES {
            return Ok(None);
        }
        let next_attempt = self.next_attempt()?;
        Self::restore(self.messages.clone(), next_attempt).map(Some)
    }

    fn next_attempt(&self) -> Result<u32, CoreError> {
        self.attempt
            .checked_add(1)
            .ok_or_else(|| CoreError::invariant("compaction request attempt sequence exhausted"))
    }

    pub(crate) fn fit_to_budget(
        mut self,
        tools: &[ToolDefinition],
        budget: CompactionBudget,
    ) -> Result<CompactionProjectionFit, CoreError> {
        let Some(token_threshold) = budget.token_threshold else {
            return Ok(CompactionProjectionFit::Fitted(self));
        };
        if estimate_model_input_tokens(&self.messages, tools, budget.image_delivery)?
            < token_threshold
        {
            return Ok(CompactionProjectionFit::Fitted(self));
        }

        let removable_history_items = self
            .messages
            .len()
            .saturating_sub(2 + usize::from(latest_preserved_user_index(&self.messages).is_some()));
        let mut lower = 1;
        let mut upper = removable_history_items;
        let mut fitted = None;
        while lower <= upper {
            let removed = lower + (upper - lower) / 2;
            let candidate = remove_oldest_history(&self.messages, removed)?;
            if estimate_model_input_tokens(&candidate, tools, budget.image_delivery)?
                < token_threshold
            {
                fitted = Some(candidate);
                upper = removed.saturating_sub(1);
            } else {
                lower = removed + 1;
            }
        }
        if let Some(fitted) = fitted {
            self.messages = fitted;
            return Ok(CompactionProjectionFit::Fitted(self));
        }

        let minimal = remove_oldest_history(&self.messages, removable_history_items)?;
        if let Some(fitted) = fit_latest_user_message_to_budget(
            &minimal,
            tools,
            token_threshold,
            budget.image_delivery,
        )? {
            self.messages = fitted;
            return Ok(CompactionProjectionFit::Fitted(self));
        }
        Ok(CompactionProjectionFit::TooLarge)
    }
}

fn remove_oldest_history(
    messages: &[StoredMessage],
    removal_count: usize,
) -> Result<Vec<StoredMessage>, CoreError> {
    let (prompt, history) = messages
        .split_last()
        .ok_or_else(|| CoreError::invariant("compaction projection is empty"))?;
    let mut candidate = history.to_vec();
    for _ in 0..removal_count {
        if candidate.len() <= 1 {
            break;
        }
        let protected_user = latest_preserved_user_index(&candidate);
        let Some(index) = (1..candidate.len()).find(|index| Some(*index) != protected_user) else {
            break;
        };
        let removed = candidate.remove(index);
        remove_corresponding_tool_message(&mut candidate, &removed.message);
    }
    candidate.push(prompt.clone());
    Ok(candidate)
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
