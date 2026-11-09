use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::core::features::notifications::{AdmittedNotification, NotificationState};

use super::schema::CheckpointNotification;

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

impl CheckpointNotifications {
    pub(super) fn capture(state: &NotificationState) -> Self {
        let mut entries = Vec::with_capacity(state.received_count());
        let pending_ids = state
            .pending()
            .map(|notification| notification.id.as_str())
            .collect::<BTreeSet<_>>();
        for entry in state.admissions() {
            let notification = &entry.notification;
            entries.push(if state.ready_for_model() {
                CheckpointNotificationState::Ready {
                    notification: CheckpointNotification::capture(notification),
                    preceding_steers: Some(entry.preceding_steers),
                }
            } else {
                CheckpointNotificationState::Pending {
                    notification: CheckpointNotification::capture(notification),
                    preceding_steers: Some(entry.preceding_steers),
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

    pub(super) fn restore(self, pending_steers: usize) -> Result<NotificationState, String> {
        let mut pending = Vec::new();
        let mut received_ids = BTreeSet::new();
        let mut received_order = Vec::new();
        let mut received_tail_started = false;
        let mut ready_for_model = None;
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
                    let (notification, position) = match entry {
                        CheckpointNotificationState::Pending {
                            notification,
                            preceding_steers,
                        }
                        | CheckpointNotificationState::Ready {
                            notification,
                            preceding_steers,
                        } => (notification, preceding_steers.unwrap_or(pending_steers)),
                        CheckpointNotificationState::Received { .. } => unreachable!(),
                    };
                    let notification = notification.restore();
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
                    received_order.push(notification.id.clone());
                    pending.push(AdmittedNotification {
                        notification,
                        preceding_steers: position,
                    });
                }
                CheckpointNotificationState::Received { id } => {
                    received_tail_started = true;
                    if !received_ids.insert(id.clone()) {
                        return Err(format!(
                            "checkpoint contains duplicate notification ID {id:?}"
                        ));
                    }
                    received_order.push(id);
                }
            }
        }
        NotificationState::try_from_parts_with_delivery(
            pending,
            ready_for_model.unwrap_or(false),
            received_order,
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
    fn capture_preserves_pending_order_and_recent_received_order() {
        let state = NotificationState::try_from_parts_with_delivery(
            vec![notification("pending-z"), notification("pending-a")]
                .into_iter()
                .map(|notification| AdmittedNotification {
                    notification,
                    preceding_steers: 0,
                })
                .collect(),
            false,
            vec![
                "received-z".to_string(),
                "pending-a".to_string(),
                "received-a".to_string(),
                "pending-z".to_string(),
            ],
        )
        .unwrap();

        let checkpoint = CheckpointNotifications::capture(&state);
        let encoded = serde_json::to_value(&checkpoint).unwrap();

        assert_eq!(
            encoded,
            json!([
                {
                    "type": "pending",
                    "preceding_steers": 0,
                    "notification": notification_json("pending-z")
                },
                {
                    "type": "pending",
                    "preceding_steers": 0,
                    "notification": notification_json("pending-a")
                },
                { "type": "received", "id": "received-z" },
                { "type": "received", "id": "received-a" }
            ])
        );
        let restored = checkpoint.restore(0).unwrap();
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
        let empty_id = json!([{ "type": "received", "id": "" }]);

        for (label, value) in [
            ("duplicate ID", duplicate),
            ("pending after received", pending_after_received),
            ("empty ID", empty_id),
        ] {
            let checkpoint: CheckpointNotifications = serde_json::from_value(value).unwrap();
            assert!(checkpoint.restore(0).is_err(), "restored {label}");
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
