//! Instructions held by key: standing text that applies until it is replaced
//! or removed.
//!
//! Core keeps no separate store. The live instructions are the newest note of
//! each key in the history and in the context waiting for the Turn, when that
//! note sets text. Setting or removing instructions appends a note, so the
//! history before it never changes. Compaction keeps the newest note of each
//! live key and drops every other note, so Core bounds the live set when a note
//! is admitted, and checks that new settings still leave room for a summary.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::features::compaction::{
    CompactionBudgets, EstimateCorrection, minimum_replacement_fits,
};
use crate::core::model_input_budget::estimate_model_input_tokens;
use crate::core::pending_input::PendingEntry;
use crate::core::prompt::build_system_prompt;
use crate::core::state::HarnessState;
use crate::core::tools::resolved::ResolvedTools;
use crate::core::wire::message::{
    InstructionsNote, Message, StoredMessage, validate_instructions_key,
};

const DEFAULT_MAX_TOKENS: u64 = 16_000;
const DEFAULT_MAX_THRESHOLD_PERCENT: u8 = 10;

/// The most the live instructions may take together, by estimate. With
/// automatic compaction, the limit is the smaller of `max_tokens` and
/// `max_threshold_percent` of the compaction threshold.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Settings {
    pub max_tokens: u64,
    pub max_threshold_percent: u8,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            max_tokens: DEFAULT_MAX_TOKENS,
            max_threshold_percent: DEFAULT_MAX_THRESHOLD_PERCENT,
        }
    }
}

impl Settings {
    pub(crate) fn is_default(&self) -> bool {
        *self == Self::default()
    }

    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if self.max_tokens == 0 || !(1..=100).contains(&self.max_threshold_percent) {
            return Err(CoreError::invalid_configuration(
                "settings.context.instructions",
                "instructions max_tokens must be positive and max_threshold_percent \
                 between 1 and 100",
            ));
        }
        Ok(())
    }

    /// The limit in estimated tokens, given the compaction threshold, if any.
    fn limit(&self, threshold: Option<u64>) -> u64 {
        threshold.map_or(self.max_tokens, |threshold| {
            self.max_tokens
                .min(threshold.saturating_mul(u64::from(self.max_threshold_percent)) / 100)
        })
    }
}

/// The text of the notes Core appends. `{key}` is replaced with the key, and
/// `{content}` in `note` and `overwrite_note` with the instructions. Every
/// note names its key, so the model can tell notes for different keys apart.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prompts {
    /// The note that delivers the live text for a key that held none, and
    /// the text compaction carries for every live key.
    pub note: String,
    /// The note that delivers the live text for a key that already held
    /// text, and says that text no longer applies.
    pub overwrite_note: String,
    /// The note that says the instructions for a key no longer apply.
    pub deletion_note: String,
}

impl Prompts {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if [&self.note, &self.overwrite_note, &self.deletion_note]
            .iter()
            .any(|text| text.trim().is_empty())
        {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.instructions",
                "instructions prompts must not be empty",
            ));
        }
        if [&self.note, &self.overwrite_note, &self.deletion_note]
            .iter()
            .any(|text| !text.contains("{key}"))
        {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.instructions",
                "the instructions note, overwrite note and deletion note must contain {key}",
            ));
        }
        if !self.note.contains("{content}") || !self.overwrite_note.contains("{content}") {
            return Err(CoreError::invalid_configuration(
                "settings.prompts.instructions",
                "the instructions note and overwrite note must contain {content}",
            ));
        }
        Ok(())
    }
}

/// Checks a key and the text given for it.
pub(crate) fn validate_upsert(key: &str, content: &str) -> Result<(), String> {
    validate_instructions_key(key)?;
    if content.trim().is_empty() {
        return Err("instructions content must not be empty".to_string());
    }
    Ok(())
}

/// The note that delivers `content` as the live text for `key`: the
/// overwrite note when the key is live, the plain note otherwise.
pub(crate) fn upsert_note(state: &HarnessState, key: String, content: &str) -> StoredMessage {
    let prompts = &state.config().settings.prompts.instructions;
    let text = render(&prompts.note, &key, content);
    if !is_live(state, &key) {
        return StoredMessage::instructions(InstructionsNote::Upsert { key }, text);
    }
    let overwrite = render(&prompts.overwrite_note, &key, content);
    StoredMessage::instructions(
        InstructionsNote::Overwrite { key, carried: text },
        overwrite,
    )
}

fn render(template: &str, key: &str, content: &str) -> String {
    // The key holds no braces, so replacing it first leaves `content` intact.
    template.replace("{key}", key).replace("{content}", content)
}

/// The note compaction carries for a live key: an overwrite note is carried
/// as the plain note for its text, since the text it replaced is gone.
fn carried_note(stored: &StoredMessage) -> StoredMessage {
    let Some(InstructionsNote::Overwrite { key, carried }) = &stored.instructions else {
        return stored.clone();
    };
    StoredMessage {
        turn_index: stored.turn_index,
        ..StoredMessage::instructions(
            InstructionsNote::Upsert { key: key.clone() },
            carried.clone(),
        )
    }
}

/// Whether `key` holds text in the history or the context waiting for the
/// Turn.
fn is_live(state: &HarnessState, key: &str) -> bool {
    let messages = session_messages(state);
    live_instructions_notes(messages.iter().copied())
        .into_iter()
        .any(|index| {
            messages[index]
                .instructions
                .as_ref()
                .map(InstructionsNote::key)
                == Some(key)
        })
}

/// The note that says the instructions for `key` no longer apply, or `None`
/// when the key is not live.
pub(crate) fn delete_note(state: &HarnessState, key: String) -> Option<StoredMessage> {
    if !is_live(state, &key) {
        return None;
    }
    let text = state
        .config()
        .settings
        .prompts
        .instructions
        .deletion_note
        .replace("{key}", &key);
    Some(StoredMessage::instructions(
        InstructionsNote::Delete { key },
        text,
    ))
}

/// The indexes, ascending, of the newest note of each live key. A key whose
/// newest note removes it has none.
fn live_instructions_notes<'m>(
    messages: impl IntoIterator<Item = &'m StoredMessage>,
) -> Vec<usize> {
    let mut newest = HashMap::new();
    for (index, stored) in messages.into_iter().enumerate() {
        if let Some(note) = &stored.instructions {
            newest.insert(note.key(), (index, note));
        }
    }
    let mut indexes = newest
        .into_values()
        .filter_map(|(index, note)| note.sets_text().then_some(index))
        .collect::<Vec<_>>();
    indexes.sort_unstable();
    indexes
}

/// The newest note of each live key among `messages`, in the order keys were
/// last updated, as compaction carries it.
pub(crate) fn live_notes<'m>(
    messages: impl IntoIterator<Item = &'m StoredMessage>,
) -> Vec<StoredMessage> {
    let messages = messages.into_iter().collect::<Vec<_>>();
    live_instructions_notes(messages.iter().copied())
        .into_iter()
        .map(|index| carried_note(messages[index]))
        .collect()
}

/// Checks the live set `notes` would leave once appended after the history
/// and the context waiting for the Turn: it must stay within the limit and
/// leave room for a summary.
pub(crate) fn admit(state: &HarnessState, notes: &[StoredMessage]) -> Result<(), CoreError> {
    let mut candidates = session_messages(state);
    candidates.extend(notes);
    let live = live_notes(candidates);
    let config = state.config();
    let correction = state.estimate_correction;
    let reason = match over_limit(config, &live, correction)? {
        Some(reason) => Some(reason),
        None => no_room_for_summary(config, state.resolved_tools(), &live, correction)?,
    };
    reason.map_or(Ok(()), |reason| {
        Err(CoreError::invalid_command(format!(
            "instructions cannot be admitted: {reason}"
        )))
    })
}

/// Checks that the Session's live instructions leave room for a summary under
/// the settings of `next`. The limit is not checked here: it bounds growth,
/// and a Session over it must still open, so its instructions can be removed.
pub(crate) fn validate_settings(
    state: &HarnessState,
    next: &HarnessConfig,
    next_tools: &ResolvedTools,
    correction: EstimateCorrection,
) -> Result<(), CoreError> {
    let live = live_notes(session_messages(state));
    if live.is_empty() {
        return Ok(());
    }
    no_room_for_summary(next, next_tools, &live, correction)?.map_or(Ok(()), |reason| {
        Err(CoreError::invalid_configuration(
            "settings.context",
            format!("the Session's instructions do not fit the new settings: {reason}"),
        ))
    })
}

/// The history, then the context the active Turn holds and the steering waiting
/// in the Session queue for its next model input.
fn session_messages(state: &HarnessState) -> Vec<&StoredMessage> {
    let pending = state.active().into_iter().flat_map(|active| {
        let steering = state
            .pending_input
            .entries()
            .iter()
            .filter_map(|entry| match entry {
                PendingEntry::Steering(message) => Some(message),
                PendingEntry::Notification(_) => None,
            });
        active.pending_context.iter().chain(steering)
    });
    state.context.messages().iter().chain(pending).collect()
}

fn estimate(
    config: &HarnessConfig,
    live: &[StoredMessage],
    correction: EstimateCorrection,
) -> Result<u64, CoreError> {
    let budget = CompactionBudgets::from_settings(&config.settings.context, correction).replacement;
    estimate_model_input_tokens(live, &[], budget.image_delivery, budget.image_cost)
}

/// Why `live` is over the limit, or `None`.
fn over_limit(
    config: &HarnessConfig,
    live: &[StoredMessage],
    correction: EstimateCorrection,
) -> Result<Option<String>, CoreError> {
    let context = &config.settings.context;
    let tokens = estimate(config, live, correction)?;
    let limit = context.instructions.limit(context.compaction_threshold());
    Ok((tokens > limit).then(|| {
        format!("the instructions would take about {tokens} tokens, over the limit of {limit}")
    }))
}

/// Why `live` leaves no room for a summary, or `None`: with automatic
/// compaction, the smallest rebuild with it must fit a summary under the
/// threshold.
fn no_room_for_summary(
    config: &HarnessConfig,
    resolved_tools: &ResolvedTools,
    live: &[StoredMessage],
    correction: EstimateCorrection,
) -> Result<Option<String>, CoreError> {
    let settings = &config.settings;
    let Some(threshold) = settings.context.compaction_threshold() else {
        return Ok(None);
    };
    let budget = CompactionBudgets::from_settings(&settings.context, correction).replacement;
    let generated_system = StoredMessage::generated_system(Message::system_text(
        build_system_prompt(config, resolved_tools.tool_group_inventory_prompt_section()),
    ));
    if minimum_replacement_fits(
        live,
        generated_system,
        resolved_tools.top_level_tools(),
        budget,
        &settings.prompts.compaction,
    )? {
        return Ok(None);
    }
    let tokens = estimate(config, live, correction)?;
    Ok(Some(format!(
        "the instructions would take about {tokens} tokens and leave no room for a \
         compaction summary under the compaction threshold of {threshold}"
    )))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_limit_is_the_smaller_of_the_cap_and_the_threshold_share() {
        let settings = Settings::default();

        assert_eq!(settings.limit(None), 16_000);
        assert_eq!(settings.limit(Some(40_000)), 4_000);
        assert_eq!(settings.limit(Some(202_144)), 16_000);
    }

    #[test]
    fn the_limits_must_be_positive_and_the_percent_at_most_100() {
        for (max_tokens, max_threshold_percent) in [(0, 10), (1, 0), (1, 101)] {
            let settings = Settings {
                max_tokens,
                max_threshold_percent,
            };
            assert!(settings.validate().is_err());
        }
        assert!(Settings::default().validate().is_ok());
    }

    #[test]
    fn every_note_must_name_the_key() {
        let defaults = crate::core::prompt::PromptSettings::default().instructions;
        for prompts in [
            Prompts {
                note: "<Instructions>\n{content}\n</Instructions>".to_string(),
                ..defaults.clone()
            },
            Prompts {
                overwrite_note: "Replaced:\n{content}".to_string(),
                ..defaults.clone()
            },
            Prompts {
                deletion_note: "Instructions no longer apply.".to_string(),
                ..defaults.clone()
            },
        ] {
            let error = prompts
                .validate()
                .expect_err("a note without {key} is refused");
            assert!(error.detail().contains("must contain {key}"), "{error:?}");
        }
    }

    #[test]
    fn the_note_and_the_overwrite_note_must_hold_the_content() {
        let defaults = crate::core::prompt::PromptSettings::default().instructions;
        assert!(defaults.validate().is_ok());
        for prompts in [
            Prompts {
                note: "{key}".to_string(),
                ..defaults.clone()
            },
            Prompts {
                overwrite_note: "{key} changed".to_string(),
                ..defaults.clone()
            },
        ] {
            assert!(prompts.validate().is_err());
        }
    }
}
