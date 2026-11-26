use serde::{Deserialize, Serialize};

use crate::core::config::HarnessConfig;
use crate::core::model_context::ModelContext;
use crate::core::prompt::build_system_prompt;
use crate::core::wire::message::{
    InstructionsNote, Message, StoredMessage, StoredMessageSource, validate_instructions_key,
};

use super::schema::CheckpointMessage;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointContext {
    messages: Vec<CheckpointStoredMessage>,
}

impl CheckpointContext {
    pub(super) fn capture(context: &ModelContext) -> Result<Self, String> {
        let mut generated_system_count = 0;
        let mut messages = Vec::with_capacity(context.message_count());
        for (index, message) in context.messages().iter().enumerate() {
            if message.is_generated_system() {
                message
                    .message
                    .validate()
                    .map_err(|error| error.detail().to_string())?;
                generated_system_count += 1;
                if index != 0 || !matches!(message.message, Message::System { .. }) {
                    return Err(
                        "generated system prompt must be the first system message".to_string()
                    );
                }
                continue;
            }
            messages.push(CheckpointStoredMessage::capture(message)?);
        }
        if !context.is_empty() && generated_system_count != 1 {
            return Err(
                "non-empty model context must contain one generated system prompt".to_string(),
            );
        }
        if !context.is_empty() && messages.is_empty() {
            return Err(
                "non-empty model context cannot contain only the generated prompt".to_string(),
            );
        }
        Ok(Self { messages })
    }

    pub(super) fn restore(
        self,
        config: &HarnessConfig,
        tool_group_inventory_prompt_section: &str,
    ) -> Result<ModelContext, String> {
        let mut messages = self
            .messages
            .into_iter()
            .map(CheckpointStoredMessage::restore)
            .collect::<Result<Vec<_>, _>>()?;
        if !messages.is_empty() {
            messages.insert(
                0,
                StoredMessage::generated_system(Message::system_text(build_system_prompt(
                    config,
                    tool_group_inventory_prompt_section,
                ))),
            );
        }
        Ok(ModelContext::restored(messages))
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointMessageSource {
    History,
    Injection,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(super) struct CheckpointStoredMessage {
    message: CheckpointMessage,
    source: CheckpointMessageSource,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    turn_index: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    instructions: Option<InstructionsNote>,
}

impl CheckpointStoredMessage {
    pub(super) fn capture(message: &StoredMessage) -> Result<Self, String> {
        message
            .message
            .validate()
            .map_err(|error| error.detail().to_string())?;
        let source = match message.source {
            StoredMessageSource::GeneratedSystem => {
                return Err("generated system prompt cannot be checkpointed as history".to_string());
            }
            StoredMessageSource::History => CheckpointMessageSource::History,
            StoredMessageSource::Injection => CheckpointMessageSource::Injection,
        };
        Ok(Self {
            message: CheckpointMessage::capture(&message.message),
            source,
            turn_index: message.turn_index,
            instructions: message.instructions.clone(),
        })
    }

    pub(super) fn restore(self) -> Result<StoredMessage, String> {
        let message = self.message.restore();
        message
            .validate()
            .map_err(|error| error.detail().to_string())?;
        if let Some(note) = &self.instructions {
            validate_instructions_key(note.key())?;
            if let InstructionsNote::Overwrite { carried, .. } = note
                && carried.trim().is_empty()
            {
                return Err("an instructions overwrite note must carry text".to_string());
            }
            if self.source != CheckpointMessageSource::Injection
                || !matches!(message, Message::User { context: true, .. })
            {
                return Err("only injected context messages can be instructions notes".to_string());
            }
        }
        Ok(StoredMessage {
            message,
            turn_index: self.turn_index,
            instructions: self.instructions,
            source: match self.source {
                CheckpointMessageSource::History => StoredMessageSource::History,
                CheckpointMessageSource::Injection => StoredMessageSource::Injection,
            },
        })
    }

    pub(super) fn capture_steering(message: &StoredMessage, label: &str) -> Result<Self, String> {
        validate_steering(message, label)?;
        Self::capture(message)
    }

    pub(super) fn restore_steering(self, label: &str) -> Result<StoredMessage, String> {
        let message = self.restore()?;
        validate_steering(&message, label)?;
        Ok(message)
    }

    pub(super) fn capture_injected_context(
        message: &StoredMessage,
        label: &str,
    ) -> Result<Self, String> {
        validate_injected_context(message, label)?;
        Self::capture(message)
    }

    pub(super) fn restore_injected_context(self, label: &str) -> Result<StoredMessage, String> {
        let message = self.restore()?;
        validate_injected_context(&message, label)?;
        Ok(message)
    }
}

/// Steering is a visible user message, or an injected context message.
fn validate_steering(message: &StoredMessage, label: &str) -> Result<(), String> {
    let valid = match (&message.source, &message.message) {
        (StoredMessageSource::History, Message::User { context, .. }) => !context,
        (StoredMessageSource::Injection, Message::User { context, .. }) => *context,
        _ => false,
    };
    if !valid {
        return Err(format!(
            "{label} must contain visible user messages or injected context messages"
        ));
    }
    Ok(())
}

fn validate_injected_context(message: &StoredMessage, label: &str) -> Result<(), String> {
    if message.source != StoredMessageSource::Injection
        || !matches!(message.message, Message::User { context: true, .. })
    {
        return Err(format!("{label} must contain injected context messages"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::wire::content::text_content;

    #[test]
    fn pending_context_restores_only_injected_context_messages() {
        let context = StoredMessage::injected(Message::context(text_content("frozen")));
        let checkpoint =
            CheckpointStoredMessage::capture_injected_context(&context, "pending context").unwrap();
        assert_eq!(
            checkpoint.restore_injected_context("pending context"),
            Ok(context)
        );

        for message in [
            StoredMessage::injected(Message::user(text_content("deploy"))),
            StoredMessage::visible(Message::context(text_content("frozen"))),
        ] {
            let checkpoint = CheckpointStoredMessage::capture(&message).unwrap();
            assert_eq!(
                checkpoint.restore_injected_context("pending context"),
                Err("pending context must contain injected context messages".to_string())
            );
            assert!(
                CheckpointStoredMessage::capture_injected_context(&message, "pending context")
                    .is_err()
            );
        }
    }

    #[test]
    fn instructions_notes_round_trip_beside_unmarked_context() {
        let previous: CheckpointStoredMessage = serde_json::from_value(serde_json::json!({
            "message": {"role": "user", "content": [{"type": "text", "text": "frozen"}], "context": true},
            "source": "injection",
        }))
        .unwrap();
        assert_eq!(
            previous.restore(),
            Ok(StoredMessage::injected(Message::context(text_content(
                "frozen"
            ))))
        );

        for (note, expected) in [
            (
                InstructionsNote::Upsert {
                    key: "model".to_string(),
                },
                serde_json::json!({"kind": "upsert", "key": "model"}),
            ),
            (
                InstructionsNote::Overwrite {
                    key: "model".to_string(),
                    carried: "plain".to_string(),
                },
                serde_json::json!({"kind": "overwrite", "key": "model", "carried": "plain"}),
            ),
            (
                InstructionsNote::Delete {
                    key: "model".to_string(),
                },
                serde_json::json!({"kind": "delete", "key": "model"}),
            ),
        ] {
            let stored = StoredMessage::instructions(note, "text".to_string());
            let value =
                serde_json::to_value(CheckpointStoredMessage::capture(&stored).unwrap()).unwrap();
            assert_eq!(value["instructions"], expected);
            let restored: CheckpointStoredMessage = serde_json::from_value(value).unwrap();
            assert_eq!(restored.restore(), Ok(stored));
        }
    }

    #[test]
    fn an_overwrite_note_without_carried_text_does_not_restore() {
        let checkpoint: CheckpointStoredMessage = serde_json::from_value(serde_json::json!({
            "message": {"role": "user", "content": [{"type": "text", "text": "deploy"}], "context": true},
            "source": "injection",
            "instructions": {"kind": "overwrite", "key": "deploys", "carried": " "},
        }))
        .unwrap();
        assert_eq!(
            checkpoint.restore(),
            Err("an instructions overwrite note must carry text".to_string())
        );
    }

    #[test]
    fn only_injected_context_messages_restore_as_instructions_notes() {
        let checkpoint: CheckpointStoredMessage = serde_json::from_value(serde_json::json!({
            "message": {"role": "user", "content": [{"type": "text", "text": "deploy"}]},
            "source": "history",
            "instructions": {"kind": "upsert", "key": "deploys"},
        }))
        .unwrap();
        assert_eq!(
            checkpoint.restore(),
            Err("only injected context messages can be instructions notes".to_string())
        );
    }
}
