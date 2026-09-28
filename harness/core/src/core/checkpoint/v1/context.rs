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
        })
    }

    pub(super) fn restore(self) -> Result<StoredMessage, String> {
        let message = self.message.restore();
        message
            .validate()
            .map_err(|error| error.detail().to_string())?;
        Ok(StoredMessage {
            message,
            source: match self.source {
                CheckpointMessageSource::History => StoredMessageSource::History,
                CheckpointMessageSource::Injection => StoredMessageSource::Injection,
            },
        })
    }

    pub(super) fn capture_visible_user(
        message: &StoredMessage,
        label: &str,
    ) -> Result<Self, String> {
        validate_visible_user(message, label)?;
        Self::capture(message)
    }

    pub(super) fn restore_visible_user(self, label: &str) -> Result<StoredMessage, String> {
        let message = self.restore()?;
        validate_visible_user(&message, label)?;
        Ok(message)
    }
}

fn validate_visible_user(message: &StoredMessage, label: &str) -> Result<(), String> {
    if message.source != StoredMessageSource::History
        || !matches!(message.message, Message::User { .. })
    {
        return Err(format!("{label} must contain visible user messages"));
    }
    Ok(())
}
