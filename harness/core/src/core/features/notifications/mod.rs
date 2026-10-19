use std::collections::{BTreeSet, VecDeque};

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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Delivery {
    Immediate,
    Deferred,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Acceptance {
    Duplicate,
    Accepted,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct NotificationState {
    pending: Vec<AdmittedNotification>,
    /// Encoded size of `pending`, kept in step so admission does not re-encode it.
    pending_bytes: usize,
    ready_for_model: bool,
    received_ids: VecDeque<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct AdmittedNotification {
    pub notification: Notification,
    /// How many of this Turn's still-pending steering messages were admitted
    /// before this notification, so Core commits both in arrival order.
    pub preceding_steers: usize,
}

impl NotificationState {
    pub(crate) fn accept(
        &mut self,
        notification: Notification,
        delivery: Delivery,
        settings: &Settings,
        preceding_steers: usize,
    ) -> Result<Acceptance, CoreError> {
        validate_admission(&notification)?;
        if self.received_ids.contains(&notification.id) {
            return Ok(Acceptance::Duplicate);
        }
        let pending_bytes = self
            .pending_bytes
            .saturating_add(encoded_size(&notification)?);
        if self.pending.len() >= settings.pending_count || pending_bytes > settings.pending_bytes {
            return Err(CoreError::QueueFull);
        }
        self.pending_bytes = pending_bytes;
        self.received_ids.push_back(notification.id.clone());
        self.pending.push(AdmittedNotification {
            notification,
            preceding_steers,
        });
        if matches!(delivery, Delivery::Immediate) {
            self.ready_for_model = true;
        }
        Ok(Acceptance::Accepted)
    }

    pub(crate) fn mark_ready(&mut self) {
        if !self.pending.is_empty() {
            self.ready_for_model = true;
        }
    }

    pub(crate) fn take_ready(&mut self, recent_limit: usize) -> Vec<AdmittedNotification> {
        if !self.ready_for_model {
            return Vec::new();
        }
        self.ready_for_model = false;
        self.pending_bytes = 0;
        let notifications = std::mem::take(&mut self.pending);
        self.retain_recent(recent_limit);
        notifications
    }

    pub(crate) fn retain_recent(&mut self, recent_limit: usize) {
        let pending_ids = self
            .pending
            .iter()
            .map(|value| value.notification.id.as_str())
            .collect::<BTreeSet<_>>();
        let mut excess = (self.received_ids.len() - pending_ids.len()).saturating_sub(recent_limit);
        self.received_ids.retain(|id| {
            if excess == 0 || pending_ids.contains(id.as_str()) {
                return true;
            }
            excess -= 1;
            false
        });
    }

    pub(crate) fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }

    pub(crate) fn pending(&self) -> impl Iterator<Item = &Notification> {
        self.pending.iter().map(|entry| &entry.notification)
    }

    pub(crate) fn admissions(&self) -> impl Iterator<Item = &AdmittedNotification> {
        self.pending.iter()
    }

    pub(crate) fn ready_for_model(&self) -> bool {
        self.ready_for_model
    }

    pub(crate) fn received_count(&self) -> usize {
        self.received_ids.len()
    }

    pub(crate) fn received_ids(&self) -> impl Iterator<Item = &str> {
        self.received_ids.iter().map(String::as_str)
    }

    pub(crate) fn try_from_parts_with_delivery(
        pending: Vec<AdmittedNotification>,
        ready_for_model: bool,
        received_ids: Vec<String>,
    ) -> Result<Self, CoreError> {
        if ready_for_model && pending.is_empty() {
            return Err(CoreError::invalid_command(
                "ready notification state must contain a pending notification",
            ));
        }
        let mut pending_ids = BTreeSet::new();
        let mut pending_bytes = 0usize;
        for entry in &pending {
            let notification = &entry.notification;
            validate_stored_notification(notification)?;
            pending_bytes = pending_bytes.saturating_add(encoded_size(notification)?);
            if !pending_ids.insert(notification.id.clone()) {
                return Err(CoreError::invalid_command(format!(
                    "duplicate pending notification ID {:?}",
                    notification.id
                )));
            }
            if !received_ids.contains(&notification.id) {
                return Err(CoreError::invalid_command(format!(
                    "pending notification {:?} is absent from received IDs",
                    notification.id
                )));
            }
        }
        if received_ids.iter().any(|id| id.trim().is_empty()) {
            return Err(CoreError::invalid_command(
                "received notification ID must not be empty",
            ));
        }
        if received_ids.iter().collect::<BTreeSet<_>>().len() != received_ids.len() {
            return Err(CoreError::invalid_command(
                "received notification IDs must be unique",
            ));
        }
        let received_ids = received_ids
            .into_iter()
            .filter(|id| !pending_ids.contains(id))
            .chain(pending.iter().map(|entry| entry.notification.id.clone()))
            .collect();
        Ok(Self {
            pending,
            pending_bytes,
            ready_for_model,
            received_ids,
        })
    }
}

fn encoded_size(notification: &Notification) -> Result<usize, CoreError> {
    serde_json::to_vec(notification)
        .map(|encoded| encoded.len())
        .map_err(|error| CoreError::invariant(error.to_string()))
}

// Admission policy is deliberately separate from permanent stored-state
// invariants. Future command-only restrictions belong here and must not make
// an existing checkpoint unreadable.
fn validate_admission(notification: &Notification) -> Result<(), CoreError> {
    validate_stored_notification(notification)
}

fn validate_stored_notification(notification: &Notification) -> Result<(), CoreError> {
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
        render_agent_message(message)?;
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
        NotificationSource::AgentMessage {
            sender_agent_path,
            kind,
        } => Some(AgentMessageContext::text(
            sender_agent_path.clone(),
            *kind,
            notification.content.clone(),
        )),
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
                render_agent_message(message)?,
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

#[cfg(test)]
mod tests {
    use super::*;

    fn notification(id: &str) -> Notification {
        Notification {
            id: id.to_string(),
            source: NotificationSource::BackgroundProcess {
                process_id: "process".to_string(),
                status: ProcessTerminalStatus::Completed,
                exit_code: None,
            },
            level: NotificationLevel::Info,
            message: "done".to_string(),
            content: Vec::new(),
        }
    }

    /// *Prepare*: A byte budget that holds exactly two equal-size notifications.
    /// *Do*: Fill it, deliver, refill, then restore the refilled queue from its parts.
    /// *Assert*: The tracked size frees on delivery and is rebuilt on restore.
    #[test]
    fn pending_byte_count_follows_delivery_and_restore() {
        // Prepare
        let size = encoded_size(&notification("n-0")).unwrap();
        let settings = Settings {
            pending_count: 100,
            pending_bytes: 2 * size,
            recent_ids: 100,
        };
        let mut state = NotificationState::default();
        let accept = |state: &mut NotificationState, id: &str| {
            state.accept(notification(id), Delivery::Immediate, &settings, 0)
        };

        // Do
        accept(&mut state, "n-0").unwrap();
        accept(&mut state, "n-1").unwrap();
        let full = accept(&mut state, "n-2");
        assert_eq!(state.take_ready(100).len(), 2);
        accept(&mut state, "n-3").unwrap();
        accept(&mut state, "n-4").unwrap();
        let mut restored = NotificationState::try_from_parts_with_delivery(
            state.admissions().cloned().collect(),
            state.ready_for_model(),
            state.received_ids().map(str::to_string).collect(),
        )
        .unwrap();
        let restored_full = accept(&mut restored, "n-5");

        // Assert
        assert!(matches!(full, Err(CoreError::QueueFull)));
        assert!(matches!(restored_full, Err(CoreError::QueueFull)));
        assert_eq!(restored, state);
        assert_eq!(restored.pending_bytes, 2 * size);
    }
}
