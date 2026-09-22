use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::core::features::notifications::NotificationState;

use super::schema::CheckpointNotification;

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(transparent)]
pub(super) struct CheckpointNotifications(Vec<CheckpointNotificationState>);

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointNotificationState {
    Pending {
        notification: CheckpointNotification,
    },
    Ready {
        notification: CheckpointNotification,
    },
    Received {
        id: String,
    },
}

impl CheckpointNotifications {
    pub(super) fn capture(state: &NotificationState) -> Self {
        let mut entries = Vec::with_capacity(state.received_count());
        let pending_ids = state
            .pending()
            .map(|notification| notification.id.as_str())
            .collect::<BTreeSet<_>>();
        for notification in state.pending() {
            entries.push(if state.ready_for_model() {
                CheckpointNotificationState::Ready {
                    notification: CheckpointNotification::capture(notification),
                }
            } else {
                CheckpointNotificationState::Pending {
                    notification: CheckpointNotification::capture(notification),
                }
            });
        }
        for id in state.received_ids() {
            if !pending_ids.contains(id) {
                entries.push(CheckpointNotificationState::Received { id: id.to_string() });
            }
        }
        Self(entries)
    }

    pub(super) fn restore(self) -> Result<NotificationState, String> {
        let mut pending = Vec::new();
        let mut received_ids = BTreeSet::new();
        let mut received_tail_started = false;
        let mut ready_for_model = None;
        let mut previous_received_id: Option<String> = None;
        for entry in self.0 {
            match entry {
                entry @ (CheckpointNotificationState::Pending { .. }
                | CheckpointNotificationState::Ready { .. }) => {
                    if received_tail_started {
                        return Err(
                            "pending checkpoint notifications must precede received entries"
                                .to_string(),
                        );
                    }
                    let entry_ready = matches!(&entry, CheckpointNotificationState::Ready { .. });
                    let notification = match entry {
                        CheckpointNotificationState::Pending { notification }
                        | CheckpointNotificationState::Ready { notification } => notification,
                        CheckpointNotificationState::Received { .. } => unreachable!(),
                    }
                    .restore();
                    if ready_for_model
                        .replace(entry_ready)
                        .is_some_and(|ready| ready != entry_ready)
                    {
                        return Err(
                            "checkpoint cannot mix pending and ready notifications".to_string()
                        );
                    }
                    if !received_ids.insert(notification.id.clone()) {
                        return Err(format!(
                            "checkpoint contains duplicate notification ID {:?}",
                            notification.id
                        ));
                    }
                    pending.push(notification);
                }
                CheckpointNotificationState::Received { id } => {
                    received_tail_started = true;
                    if !received_ids.insert(id.clone()) {
                        return Err(format!(
                            "checkpoint contains duplicate notification ID {id:?}"
                        ));
                    }
                    if previous_received_id
                        .as_ref()
                        .is_some_and(|previous| previous >= &id)
                    {
                        return Err(
                            "received checkpoint notification IDs must be strictly sorted"
                                .to_string(),
                        );
                    }
                    previous_received_id = Some(id);
                }
            }
        }
        NotificationState::try_from_parts_with_delivery(
            pending,
            ready_for_model.unwrap_or(false),
            received_ids,
        )
        .map_err(|error| error.detail().to_string())
    }
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::core::features::notifications::Notification;
    use crate::core::features::notifications::{
        AsyncToolNotificationStatus, NotificationLevel, NotificationSource,
    };

    #[test]
    fn capture_preserves_pending_order_and_sorts_received_only_ids() {
        let state = NotificationState::try_from_parts_with_delivery(
            vec![notification("pending-z"), notification("pending-a")],
            false,
            BTreeSet::from([
                "received-z".to_string(),
                "pending-a".to_string(),
                "received-a".to_string(),
                "pending-z".to_string(),
            ]),
        )
        .unwrap();

        let checkpoint = CheckpointNotifications::capture(&state);
        let encoded = serde_json::to_value(&checkpoint).unwrap();

        assert_eq!(
            encoded,
            json!([
                {
                    "type": "pending",
                    "notification": notification_json("pending-z")
                },
                {
                    "type": "pending",
                    "notification": notification_json("pending-a")
                },
                { "type": "received", "id": "received-a" },
                { "type": "received", "id": "received-z" }
            ])
        );
        let restored = checkpoint.restore().unwrap();
        assert_eq!(restored, state);
        assert_eq!(
            serde_json::to_value(CheckpointNotifications::capture(&restored)).unwrap(),
            encoded
        );
    }

    #[test]
    fn restore_rejects_duplicate_or_noncanonical_notification_entries() {
        let duplicate = json!([
            {
                "type": "pending",
                "notification": notification_json("notification-1")
            },
            { "type": "received", "id": "notification-1" }
        ]);
        let pending_after_received = json!([
            { "type": "received", "id": "notification-1" },
            {
                "type": "pending",
                "notification": notification_json("notification-2")
            }
        ]);
        let unsorted_received = json!([
            { "type": "received", "id": "notification-z" },
            { "type": "received", "id": "notification-a" }
        ]);
        let empty_id = json!([{ "type": "received", "id": "" }]);

        for (label, value) in [
            ("duplicate ID", duplicate),
            ("pending after received", pending_after_received),
            ("unsorted received IDs", unsorted_received),
            ("empty ID", empty_id),
        ] {
            let checkpoint: CheckpointNotifications = serde_json::from_value(value).unwrap();
            assert!(checkpoint.restore().is_err(), "restored {label}");
        }
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

    fn notification_json(id: &str) -> serde_json::Value {
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
