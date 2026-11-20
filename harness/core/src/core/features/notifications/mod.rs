use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::core::error::CoreError;
use crate::core::helpers::{AgentMessageContext, render_agent_message};
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{Message, StoredMessage};

mod model;

pub(crate) use model::{
    AsyncToolNotificationStatus, Notification, NotificationLevel, NotificationSource,
    ProcessTerminalStatus, SubagentNotificationStatus,
};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Settings {
    pub pending_count: usize,
    pub pending_bytes: usize,
    pub recent_ids: usize,
}

impl Settings {
    pub(crate) fn validate(&self) -> Result<(), CoreError> {
        if self.pending_count == 0 || self.pending_bytes == 0 || self.recent_ids == 0 {
            return Err(CoreError::invalid_configuration(
                "settings.context.notifications",
                "notification count, byte, and recent-identity limits must be positive",
            ));
        }
        Ok(())
    }
}

pub(crate) fn encoded_size(notification: &Notification) -> Result<usize, CoreError> {
    serde_json::to_vec(notification)
        .map(|encoded| encoded.len())
        .map_err(|error| CoreError::invariant(error.to_string()))
}

// Admission policy is deliberately separate from permanent stored-state
// invariants. Future command-only restrictions belong here and must not make
// an existing checkpoint unreadable.
pub(crate) fn validate_admission(notification: &Notification) -> Result<(), CoreError> {
    validate_stored_notification(notification)
}

pub(crate) fn validate_stored_notification(notification: &Notification) -> Result<(), CoreError> {
    if notification.id.trim().is_empty() {
        return Err(CoreError::invalid_command(
            "notification id must not be empty",
        ));
    }
    if notification.message.trim().is_empty() {
        return Err(CoreError::invalid_command(
            "notification message must not be empty",
        ));
    }
    if let Some(message) = attributed_agent_context(notification) {
        render_agent_message(
            message,
            &crate::core::prompt::PromptSettings::default().agent_messages,
            true,
        )?;
        return Ok(());
    }
    match &notification.source {
        NotificationSource::BackgroundProcess { process_id, .. }
            if process_id.trim().is_empty() =>
        {
            Err(CoreError::invalid_command(
                "notification process_id must not be empty",
            ))
        }
        NotificationSource::Subagent { agent_name, .. } if agent_name.trim().is_empty() => Err(
            CoreError::invalid_command("notification agent_name must not be empty"),
        ),
        NotificationSource::AsyncTool { call_id, .. } if call_id.trim().is_empty() => Err(
            CoreError::invalid_command("notification call_id must not be empty"),
        ),
        _ => Ok(()),
    }
}

fn attributed_agent_context(notification: &Notification) -> Option<AgentMessageContext> {
    match &notification.source {
        NotificationSource::AgentMessage { sender_agent_path } => {
            Some(AgentMessageContext::Message {
                sender_agent_path: sender_agent_path.clone(),
                content: notification.content.clone(),
            })
        }
        NotificationSource::AgentResult {
            sender_agent_path,
            termination,
        } => Some(AgentMessageContext::Result {
            sender_agent_path: sender_agent_path.clone(),
            termination: termination.clone(),
        }),
        _ => None,
    }
}

pub(crate) fn notification_messages(
    notifications: &[Notification],
    settings: &crate::core::helpers::AgentMessageSettings,
    continue_hint: bool,
) -> Result<Vec<StoredMessage>, CoreError> {
    let mut messages = Vec::new();
    let mut content = Vec::new();
    for notification in notifications {
        if let Some(message) = attributed_agent_context(notification) {
            if !content.is_empty() {
                messages.push(StoredMessage::injected(Message::user(std::mem::take(
                    &mut content,
                ))));
            }
            messages.push(StoredMessage::injected(Message::user(
                render_agent_message(message, settings, continue_hint)?,
            )));
            continue;
        }
        let summary = json!({
            "id": notification.id,
            "source": notification.source,
            "level": notification.level,
            "message": notification.message,
        });
        content.push(ContentBlock::text(format!(
            "Runtime notification:\n{summary}"
        )));
        content.extend(notification.content.clone());
    }
    if !content.is_empty() {
        messages.push(StoredMessage::injected(Message::user(content)));
    }
    Ok(messages)
}
