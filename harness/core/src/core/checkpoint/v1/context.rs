use serde::{Deserialize, Serialize};

use crate::core::config::HarnessConfig;
use crate::core::model_context::ModelContext;
use crate::core::prompt::build_system_prompt;
use crate::core::wire::message::{Message, StoredMessage, StoredMessageSource};

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
        })
    }

    pub(super) fn restore(self) -> Result<StoredMessage, String> {
        let message = self.message.restore();
        message
            .validate()
            .map_err(|error| error.detail().to_string())?;
        Ok(StoredMessage {
            message,
            turn_index: self.turn_index,
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
}
