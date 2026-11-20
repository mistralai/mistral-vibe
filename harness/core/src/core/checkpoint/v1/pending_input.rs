use serde::{Deserialize, Serialize};

use crate::core::pending_input::{PendingEntry, PendingInput};

use super::context::CheckpointStoredMessage;
use super::schema::CheckpointNotification;

const STEERING_LABEL: &str = "pending steering";

/// Steering and notifications not yet committed to model context, in
/// admission order.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(transparent)]
pub(super) struct CheckpointPendingInput(Vec<CheckpointPendingEntry>);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointPendingEntry {
    Steering {
        message: CheckpointStoredMessage,
    },
    Notification {
        notification: CheckpointNotification,
    },
}

/// Notification identities kept for duplicate suppression, oldest first.
///
/// Current captures write only `received` entries. Earlier V1 captures also
/// stored pending notifications here as `pending` or `ready` entries; restore
/// moves them into the pending input queue.
#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(transparent)]
pub(super) struct CheckpointNotifications(Vec<CheckpointNotificationState>);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointNotificationState {
    Pending {
        notification: CheckpointNotification,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        preceding_steers: Option<usize>,
    },
    Ready {
        notification: CheckpointNotification,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        preceding_steers: Option<usize>,
    },
    Received {
        id: String,
    },
}

impl CheckpointPendingInput {
    pub(super) fn is_empty(&self) -> bool {
        self.0.is_empty()
    }

    pub(super) fn capture(state: &PendingInput) -> Result<(Self, CheckpointNotifications), String> {
        let entries = state
            .entries()
            .iter()
            .map(|entry| {
                Ok(match entry {
                    PendingEntry::Steering(message) => CheckpointPendingEntry::Steering {
                        message: CheckpointStoredMessage::capture_steering(
                            message,
                            STEERING_LABEL,
                        )?,
                    },
                    PendingEntry::Notification(notification) => {
                        CheckpointPendingEntry::Notification {
                            notification: CheckpointNotification::capture(notification),
                        }
                    }
                })
            })
            .collect::<Result<_, String>>()?;
        let received = state
            .delivered_ids()
            .map(|id| CheckpointNotificationState::Received { id: id.to_string() })
            .collect();
        Ok((Self(entries), CheckpointNotifications(received)))
    }

    /// Restores the queue. An earlier V1 capture has no `pending_input`: it
    /// kept steering on the active Turn and pending notifications in
    /// `notifications`, each with the number of steering messages admitted
    /// before it (`preceding_steers`, absent meaning after all of them).
    /// Restore rebuilds the one queue in that admission order.
    pub(super) fn restore(
        self,
        legacy_steering: Vec<CheckpointStoredMessage>,
        notifications: CheckpointNotifications,
    ) -> Result<PendingInput, String> {
        let mut legacy_notifications = Vec::new();
        let mut delivered_ids = Vec::new();
        for entry in notifications.0 {
            match entry {
                CheckpointNotificationState::Pending {
                    notification,
                    preceding_steers,
                }
                | CheckpointNotificationState::Ready {
                    notification,
                    preceding_steers,
                } => legacy_notifications.push((notification, preceding_steers)),
                CheckpointNotificationState::Received { id } => delivered_ids.push(id),
            }
        }
        let legacy = interleave_legacy(legacy_steering, legacy_notifications);
        if !legacy.is_empty() && !self.0.is_empty() {
            return Err(
                "checkpoint mixes pending input with earlier pending steering or notifications"
                    .to_string(),
            );
        }
        let entries = legacy
            .into_iter()
            .chain(self.0)
            .map(|entry| {
                Ok(match entry {
                    CheckpointPendingEntry::Steering { message } => {
                        PendingEntry::Steering(message.restore_steering(STEERING_LABEL)?)
                    }
                    CheckpointPendingEntry::Notification { notification } => {
                        PendingEntry::Notification(notification.restore())
                    }
                })
            })
            .collect::<Result<_, String>>()?;
        PendingInput::try_from_parts(entries, delivered_ids)
            .map_err(|error| error.detail().to_string())
    }
}

/// Merges an earlier capture's steering and notifications into admission
/// order. Each notification goes after the number of steering messages it
/// recorded, capped at the steering count; notifications keep their stored
/// order.
fn interleave_legacy(
    steering: Vec<CheckpointStoredMessage>,
    notifications: Vec<(CheckpointNotification, Option<usize>)>,
) -> Vec<CheckpointPendingEntry> {
    let steering_count = steering.len();
    let mut notifications = notifications.into_iter().peekable();
    let mut entries = Vec::new();
    for (position, message) in steering.into_iter().enumerate() {
        while let Some((notification, _)) = notifications.next_if(|(_, preceding)| {
            preceding.map_or(steering_count, |count| count.min(steering_count)) <= position
        }) {
            entries.push(CheckpointPendingEntry::Notification { notification });
        }
        entries.push(CheckpointPendingEntry::Steering { message });
    }
    entries.extend(
        notifications
            .map(|(notification, _)| CheckpointPendingEntry::Notification { notification }),
    );
    entries
}

#[cfg(test)]
mod tests {
    use serde_json::{Value, json};

    use super::*;
    use crate::core::features::notifications::Notification;
    use crate::core::features::notifications::{
        AsyncToolNotificationStatus, NotificationLevel, NotificationSource,
    };
    use crate::core::wire::content::text_content;
    use crate::core::wire::message::{Message, StoredMessage};

    /// *Prepare*: A queue with steering between two notifications and a delivered identity.
    /// *Do*: Capture, encode, decode, and restore it.
    /// *Assert*: Admission order and recent identities survive unchanged.
    #[test]
    fn capture_keeps_admission_order_and_recent_identities() {
        // Prepare
        let state = PendingInput::try_from_parts(
            vec![
                PendingEntry::Notification(notification("pending-z")),
                PendingEntry::Steering(StoredMessage::visible(Message::user_text("steer"))),
                PendingEntry::Notification(notification("pending-a")),
            ],
            vec!["received-z".to_string(), "received-a".to_string()],
        )
        .unwrap();

        // Do
        let (pending, notifications) = CheckpointPendingInput::capture(&state).unwrap();
        let encoded = (
            serde_json::to_value(&pending).unwrap(),
            serde_json::to_value(&notifications).unwrap(),
        );
        let restored = pending.restore(Vec::new(), notifications).unwrap();

        // Assert
        assert_eq!(encoded.0[1]["type"], "steering");
        assert_eq!(
            encoded.0[0],
            json!({"type": "notification", "notification": notification_json("pending-z")})
        );
        assert_eq!(
            encoded.1,
            json!([
                { "type": "received", "id": "received-z" },
                { "type": "received", "id": "received-a" }
            ])
        );
        assert_eq!(restored, state);
    }

    /// *Prepare*: An earlier V1 capture with two active-turn steering messages and notifications that recorded 0, 1, no, and too many preceding steering messages.
    /// *Do*: Decode and restore it.
    /// *Assert*: The queue rebuilds admission order; a missing or too-large count places the notification after all steering.
    #[test]
    fn earlier_capture_restores_admission_order_from_preceding_steers() {
        // Prepare
        let steering = |text: &str| -> CheckpointStoredMessage {
            serde_json::from_value(json!({
                "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                "source": "history"
            }))
            .unwrap()
        };
        let notifications = legacy_notifications(json!([
            { "type": "ready", "notification": notification_json("before-first"), "preceding_steers": 0 },
            { "type": "ready", "notification": notification_json("after-first"), "preceding_steers": 1 },
            { "type": "ready", "notification": notification_json("unrecorded") },
            { "type": "ready", "notification": notification_json("too-many"), "preceding_steers": 5 },
            { "type": "received", "id": "older" }
        ]));

        // Do
        let restored = CheckpointPendingInput::default()
            .restore(vec![steering("first"), steering("second")], notifications)
            .unwrap();

        // Assert
        let steer =
            |text: &str| PendingEntry::Steering(StoredMessage::visible(Message::user_text(text)));
        assert_eq!(
            restored.entries(),
            [
                PendingEntry::Notification(notification("before-first")),
                steer("first"),
                PendingEntry::Notification(notification("after-first")),
                steer("second"),
                PendingEntry::Notification(notification("unrecorded")),
                PendingEntry::Notification(notification("too-many")),
            ]
        );
        assert_eq!(restored.delivered_ids().collect::<Vec<_>>(), ["older"]);
    }

    /// *Prepare*: A queue with context that arrived after a notification and before a steer, and an earlier V1 capture that held the same context in its pending steering.
    /// *Do*: Capture and restore the queue, and restore the earlier capture.
    /// *Assert*: Both keep the context as an injected context entry, in admission order.
    #[test]
    fn context_after_pending_input_keeps_its_place() {
        // Prepare
        let context = StoredMessage::injected(Message::context(text_content("hook context")));
        let steer = StoredMessage::visible(Message::user_text("steer"));
        let state = PendingInput::try_from_parts(
            vec![
                PendingEntry::Notification(notification("pending")),
                PendingEntry::Steering(context.clone()),
                PendingEntry::Steering(steer.clone()),
            ],
            Vec::new(),
        )
        .unwrap();
        let earlier_steering = [&context, &steer]
            .into_iter()
            .map(|message| CheckpointStoredMessage::capture_steering(message, STEERING_LABEL))
            .collect::<Result<Vec<_>, _>>()
            .unwrap();
        let earlier_notifications = legacy_notifications(json!([
            { "type": "ready", "notification": notification_json("pending"), "preceding_steers": 0 }
        ]));

        // Do
        let (pending, notifications) = CheckpointPendingInput::capture(&state).unwrap();
        let encoded = serde_json::to_value(&pending).unwrap();
        let restored = pending.restore(Vec::new(), notifications).unwrap();
        let earlier = CheckpointPendingInput::default()
            .restore(earlier_steering, earlier_notifications)
            .unwrap();

        // Assert
        assert_eq!(encoded[1]["type"], "steering");
        assert_eq!(encoded[1]["message"]["source"], "injection");
        assert_eq!(restored, state);
        assert_eq!(earlier.entries(), state.entries());
    }

    /// *Prepare*: Notification arrays that repeat or blank an identity, or mix both encodings.
    /// *Do*: Restore each.
    /// *Assert*: Restore rejects every one.
    #[test]
    fn restore_rejects_duplicate_empty_or_mixed_entries() {
        let pending: CheckpointPendingInput = serde_json::from_value(json!([
            { "type": "notification", "notification": notification_json("current") }
        ]))
        .unwrap();
        for (label, current, value) in [
            (
                "duplicate ID",
                CheckpointPendingInput::default(),
                json!([
                    { "type": "pending", "notification": notification_json("n-1") },
                    { "type": "received", "id": "n-1" }
                ]),
            ),
            (
                "empty ID",
                CheckpointPendingInput::default(),
                json!([{ "type": "received", "id": "" }]),
            ),
            (
                "mixed encodings",
                pending,
                json!([{ "type": "pending", "notification": notification_json("n-2") }]),
            ),
        ] {
            let restored = current.restore(Vec::new(), legacy_notifications(value));
            assert!(restored.is_err(), "restored {label}");
        }
    }

    fn legacy_notifications(value: Value) -> CheckpointNotifications {
        serde_json::from_value(value).unwrap()
    }

    fn notification(id: &str) -> Notification {
        Notification {
            id: id.to_string(),
            source: NotificationSource::AsyncTool {
                call_id: "call-1".to_string(),
                status: AsyncToolNotificationStatus::Completed,
            },
            level: NotificationLevel::Info,
            message: "done".to_string(),
            content: Vec::new(),
        }
    }

    fn notification_json(id: &str) -> Value {
        json!({
            "id": id,
            "source": {
                "type": "async_tool",
                "call_id": "call-1",
                "status": "completed"
            },
            "level": "info",
            "message": "done"
        })
    }
}
