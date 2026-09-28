use std::collections::BTreeSet;

use serde_json::json;

use crate::core::error::CoreError;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::message::{Message, StoredMessage};

mod model;

pub(crate) use model::{
    AsyncToolNotificationStatus, Notification, NotificationLevel, NotificationSource,
    ProcessTerminalStatus, SubagentNotificationStatus,
};

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
    pending: Vec<Notification>,
    ready_for_model: bool,
    received_ids: BTreeSet<String>,
}

impl NotificationState {
    pub(crate) fn accept(
        &mut self,
        notification: Notification,
        delivery: Delivery,
    ) -> Result<Acceptance, CoreError> {
        validate_admission(&notification)?;
        if !self.received_ids.insert(notification.id.clone()) {
            return Ok(Acceptance::Duplicate);
        }
        self.pending.push(notification);
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

    pub(crate) fn take_ready(&mut self) -> Vec<Notification> {
        if !self.ready_for_model {
            return Vec::new();
        }
        self.ready_for_model = false;
        std::mem::take(&mut self.pending)
    }

    pub(crate) fn has_pending(&self) -> bool {
        !self.pending.is_empty()
    }

    pub(crate) fn pending(&self) -> impl Iterator<Item = &Notification> {
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
        pending: Vec<Notification>,
        ready_for_model: bool,
        received_ids: BTreeSet<String>,
    ) -> Result<Self, CoreError> {
        if ready_for_model && pending.is_empty() {
            return Err(CoreError::invalid_command(
                "ready notification state must contain a pending notification",
            ));
        }
        let mut pending_ids = BTreeSet::new();
        for notification in &pending {
            validate_stored_notification(notification)?;
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
        Ok(Self {
            pending,
            ready_for_model,
            received_ids,
        })
    }
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

pub(crate) fn notification_message(notifications: &[Notification]) -> Option<StoredMessage> {
    if notifications.is_empty() {
        return None;
    }
    let mut content = Vec::new();
    for notification in notifications {
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
    Some(StoredMessage::injected(Message::user(content)))
}
