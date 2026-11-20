//! Input admitted for the model but not yet committed to its context.
//!
//! Steering and notifications share one queue in admission order, so Core
//! commits them in the order they arrived without tracking positions between
//! two collections. The queue belongs to the Session: notifications outlive a
//! Turn, while steering only exists while its Turn is active because ending a
//! Turn commits it (see [`PendingInput::take_steering`]).

use std::collections::{BTreeSet, VecDeque};

use crate::core::error::CoreError;
use crate::core::features::notifications::{
    Notification, NotificationSource, Settings as NotificationSettings, encoded_size,
    validate_admission, validate_stored_notification,
};
use crate::core::wire::message::StoredMessage;

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum PendingEntry {
    Steering(StoredMessage),
    Notification(Notification),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Acceptance {
    Duplicate,
    Accepted,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct PendingInput {
    entries: Vec<PendingEntry>,
    /// Count and encoded size of pending notifications, kept in step so
    /// admission checks the notification limits without re-encoding the queue.
    notification_count: usize,
    notification_bytes: usize,
    /// Notification identities used for duplicate suppression, oldest first.
    /// Pending identities are always present; delivered ones are bounded.
    received_ids: VecDeque<String>,
}

impl PendingInput {
    pub(crate) fn push_steering(&mut self, message: StoredMessage) {
        self.entries.push(PendingEntry::Steering(message));
    }

    pub(crate) fn accept_notification(
        &mut self,
        notification: Notification,
        settings: &NotificationSettings,
    ) -> Result<Acceptance, CoreError> {
        validate_admission(&notification)?;
        if self.received_ids.contains(&notification.id) {
            return Ok(Acceptance::Duplicate);
        }
        let bytes = self
            .notification_bytes
            .saturating_add(encoded_size(&notification)?);
        if self.notification_count >= settings.pending_count || bytes > settings.pending_bytes {
            return Err(CoreError::QueueFull);
        }
        self.notification_count += 1;
        self.notification_bytes = bytes;
        self.received_ids.push_back(notification.id.clone());
        self.entries.push(PendingEntry::Notification(notification));
        Ok(Acceptance::Accepted)
    }

    /// Removes every pending entry for commit to model context.
    pub(crate) fn take_all(&mut self, recent_limit: usize) -> Vec<PendingEntry> {
        self.notification_count = 0;
        self.notification_bytes = 0;
        let entries = std::mem::take(&mut self.entries);
        // The Runtime's process record, not Core, keeps a delivered process
        // notification from being submitted again.
        let delivered_process_ids = notifications(&entries)
            .filter(|notification| {
                matches!(
                    notification.source,
                    NotificationSource::BackgroundProcess { .. }
                )
            })
            .map(|notification| notification.id.as_str())
            .collect::<BTreeSet<_>>();
        self.received_ids
            .retain(|id| !delivered_process_ids.contains(id.as_str()));
        self.retain_recent(recent_limit);
        entries
    }

    /// Removes pending steering and leaves notifications queued.
    ///
    /// A Turn that ends before its next model call still owes its steering to
    /// model context, because clients have already shown it to the user.
    /// Notifications belong to the Session and wait for the next model call.
    pub(crate) fn take_steering(&mut self) -> Vec<StoredMessage> {
        let mut steering = Vec::new();
        self.entries.retain(|entry| match entry {
            PendingEntry::Steering(message) => {
                steering.push(message.clone());
                false
            }
            PendingEntry::Notification(_) => true,
        });
        steering
    }

    pub(crate) fn retain_recent(&mut self, recent_limit: usize) {
        let pending_ids = pending_notification_ids(&self.entries);
        let mut excess = (self.received_ids.len() - pending_ids.len()).saturating_sub(recent_limit);
        self.received_ids.retain(|id| {
            if excess == 0 || pending_ids.contains(id.as_str()) {
                return true;
            }
            excess -= 1;
            false
        });
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub(crate) fn has_steering(&self) -> bool {
        self.entries
            .iter()
            .any(|entry| matches!(entry, PendingEntry::Steering(_)))
    }

    pub(crate) fn entries(&self) -> &[PendingEntry] {
        &self.entries
    }

    /// Delivered notification identities still kept for duplicate
    /// suppression, oldest first. Pending identities are implied by the queue.
    pub(crate) fn delivered_ids(&self) -> impl Iterator<Item = &str> {
        let pending_ids = pending_notification_ids(&self.entries);
        self.received_ids
            .iter()
            .map(String::as_str)
            .filter(move |id| !pending_ids.contains(id))
    }

    pub(crate) fn try_from_parts(
        entries: Vec<PendingEntry>,
        delivered_ids: Vec<String>,
    ) -> Result<Self, CoreError> {
        let mut received = BTreeSet::new();
        for id in &delivered_ids {
            if id.trim().is_empty() {
                return Err(CoreError::invalid_command(
                    "received notification ID must not be empty",
                ));
            }
            if !received.insert(id.as_str()) {
                return Err(CoreError::invalid_command(format!(
                    "duplicate notification ID {id:?}"
                )));
            }
        }
        let mut notification_count = 0usize;
        let mut notification_bytes = 0usize;
        let mut pending_ids = Vec::new();
        for entry in &entries {
            let PendingEntry::Notification(notification) = entry else {
                continue;
            };
            validate_stored_notification(notification)?;
            if !received.insert(notification.id.as_str()) {
                return Err(CoreError::invalid_command(format!(
                    "duplicate notification ID {:?}",
                    notification.id
                )));
            }
            notification_count += 1;
            notification_bytes = notification_bytes.saturating_add(encoded_size(notification)?);
            pending_ids.push(notification.id.clone());
        }
        Ok(Self {
            entries,
            notification_count,
            notification_bytes,
            received_ids: delivered_ids.into_iter().chain(pending_ids).collect(),
        })
    }
}

fn notifications(entries: &[PendingEntry]) -> impl Iterator<Item = &Notification> {
    entries.iter().filter_map(|entry| match entry {
        PendingEntry::Notification(notification) => Some(notification),
        PendingEntry::Steering(_) => None,
    })
}

fn pending_notification_ids(entries: &[PendingEntry]) -> BTreeSet<&str> {
    notifications(entries)
        .map(|notification| notification.id.as_str())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::features::notifications::{NotificationLevel, ProcessTerminalStatus};
    use crate::core::wire::message::Message;

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

    fn steering(text: &str) -> StoredMessage {
        StoredMessage::visible(Message::user_text(text))
    }

    fn restored(queue: &PendingInput) -> PendingInput {
        PendingInput::try_from_parts(
            queue.entries().to_vec(),
            queue.delivered_ids().map(str::to_string).collect(),
        )
        .unwrap()
    }

    /// *Prepare*: A byte budget that holds exactly two equal-size notifications.
    /// *Do*: Fill it around steering, deliver, refill, then restore the refilled queue.
    /// *Assert*: Steering never counts against the budget; the size frees on delivery and is rebuilt on restore.
    #[test]
    fn notification_budget_ignores_steering_and_follows_delivery_and_restore() {
        // Prepare
        let size = encoded_size(&notification("n-0")).unwrap();
        let settings = NotificationSettings {
            pending_count: 100,
            pending_bytes: 2 * size,
            recent_ids: 100,
        };
        let mut queue = PendingInput::default();
        let accept = |queue: &mut PendingInput, id: &str| {
            queue.accept_notification(notification(id), &settings)
        };

        // Do
        queue.push_steering(steering("first"));
        accept(&mut queue, "n-0").unwrap();
        queue.push_steering(steering("second"));
        accept(&mut queue, "n-1").unwrap();
        let full = accept(&mut queue, "n-2");
        assert_eq!(queue.take_all(100).len(), 4);
        accept(&mut queue, "n-3").unwrap();
        accept(&mut queue, "n-4").unwrap();
        let mut restored = restored(&queue);
        let restored_full = accept(&mut restored, "n-5");

        // Assert
        assert!(matches!(full, Err(CoreError::QueueFull)));
        assert!(matches!(restored_full, Err(CoreError::QueueFull)));
        assert_eq!(restored, queue);
        assert_eq!(restored.notification_bytes, 2 * size);
    }

    /// *Prepare*: Steering and notifications interleaved in one queue.
    /// *Do*: End the Turn by taking its steering.
    /// *Assert*: Steering leaves in order; notifications stay pending in order and keep their budget.
    #[test]
    fn turn_end_takes_steering_and_keeps_notifications() {
        // Prepare
        let settings = NotificationSettings {
            pending_count: 2,
            pending_bytes: 1 << 20,
            recent_ids: 100,
        };
        let mut queue = PendingInput::default();
        queue
            .accept_notification(notification("n-0"), &settings)
            .unwrap();
        queue.push_steering(steering("first"));
        queue
            .accept_notification(notification("n-1"), &settings)
            .unwrap();
        queue.push_steering(steering("second"));

        // Do
        let taken = queue.take_steering();

        // Assert
        assert_eq!(taken, vec![steering("first"), steering("second")]);
        assert!(!queue.has_steering());
        assert_eq!(
            notifications(queue.entries())
                .map(|n| n.id.as_str())
                .collect::<Vec<_>>(),
            ["n-0", "n-1"]
        );
        assert!(matches!(
            queue.accept_notification(notification("n-2"), &settings),
            Err(CoreError::QueueFull)
        ));
        assert_eq!(restored(&queue), queue);
    }

    /// *Prepare*: Restore parts that repeat one notification identity.
    /// *Do*: Rebuild the queue.
    /// *Assert*: Restore rejects every duplicate placement.
    #[test]
    fn restore_rejects_duplicate_notification_ids() {
        for (entries, delivered) in [
            (
                vec![
                    PendingEntry::Notification(notification("n")),
                    PendingEntry::Notification(notification("n")),
                ],
                vec![],
            ),
            (
                vec![PendingEntry::Notification(notification("n"))],
                vec!["n".to_string()],
            ),
            (vec![], vec!["n".to_string(), "n".to_string()]),
            (vec![], vec![" ".to_string()]),
        ] {
            assert!(PendingInput::try_from_parts(entries, delivered).is_err());
        }
    }
}
