use serde::{Deserialize, Serialize};

use crate::core::features::notifications::Notification;
use crate::core::features::notifications::{
    AsyncToolNotificationStatus, NotificationLevel, NotificationSource, ProcessTerminalStatus,
    SubagentNotificationStatus,
};
use crate::core::wire::content::ContentBlock;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointProcessTerminalStatus {
    Completed,
    Failed,
    Stopped,
    Orphaned,
}

impl CheckpointProcessTerminalStatus {
    fn capture(status: &ProcessTerminalStatus) -> Self {
        match status {
            ProcessTerminalStatus::Completed => Self::Completed,
            ProcessTerminalStatus::Failed => Self::Failed,
            ProcessTerminalStatus::Stopped => Self::Stopped,
            ProcessTerminalStatus::Orphaned => Self::Orphaned,
        }
    }

    fn restore(self) -> ProcessTerminalStatus {
        match self {
            Self::Completed => ProcessTerminalStatus::Completed,
            Self::Failed => ProcessTerminalStatus::Failed,
            Self::Stopped => ProcessTerminalStatus::Stopped,
            Self::Orphaned => ProcessTerminalStatus::Orphaned,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointSubagentNotificationStatus {
    Completed,
    Failed,
    Interrupted,
}

impl CheckpointSubagentNotificationStatus {
    fn capture(status: &SubagentNotificationStatus) -> Self {
        match status {
            SubagentNotificationStatus::Completed => Self::Completed,
            SubagentNotificationStatus::Failed => Self::Failed,
            SubagentNotificationStatus::Interrupted => Self::Interrupted,
        }
    }

    fn restore(self) -> SubagentNotificationStatus {
        match self {
            Self::Completed => SubagentNotificationStatus::Completed,
            Self::Failed => SubagentNotificationStatus::Failed,
            Self::Interrupted => SubagentNotificationStatus::Interrupted,
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointAsyncToolNotificationStatus {
    Completed,
    Failed,
}

impl CheckpointAsyncToolNotificationStatus {
    fn capture(status: &AsyncToolNotificationStatus) -> Self {
        match status {
            AsyncToolNotificationStatus::Completed => Self::Completed,
            AsyncToolNotificationStatus::Failed => Self::Failed,
        }
    }

    fn restore(self) -> AsyncToolNotificationStatus {
        match self {
            Self::Completed => AsyncToolNotificationStatus::Completed,
            Self::Failed => AsyncToolNotificationStatus::Failed,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
enum CheckpointNotificationSource {
    BackgroundProcess {
        process_id: String,
        status: CheckpointProcessTerminalStatus,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        exit_code: Option<i32>,
    },
    Subagent {
        agent_name: String,
        status: CheckpointSubagentNotificationStatus,
    },
    AsyncTool {
        call_id: String,
        status: CheckpointAsyncToolNotificationStatus,
    },
}

impl CheckpointNotificationSource {
    fn capture(source: &NotificationSource) -> Self {
        match source {
            NotificationSource::BackgroundProcess {
                process_id,
                status,
                exit_code,
            } => Self::BackgroundProcess {
                process_id: process_id.clone(),
                status: CheckpointProcessTerminalStatus::capture(status),
                exit_code: *exit_code,
            },
            NotificationSource::Subagent { agent_name, status } => Self::Subagent {
                agent_name: agent_name.clone(),
                status: CheckpointSubagentNotificationStatus::capture(status),
            },
            NotificationSource::AsyncTool { call_id, status } => Self::AsyncTool {
                call_id: call_id.clone(),
                status: CheckpointAsyncToolNotificationStatus::capture(status),
            },
        }
    }

    fn restore(self) -> NotificationSource {
        match self {
            Self::BackgroundProcess {
                process_id,
                status,
                exit_code,
            } => NotificationSource::BackgroundProcess {
                process_id,
                status: status.restore(),
                exit_code,
            },
            Self::Subagent { agent_name, status } => NotificationSource::Subagent {
                agent_name,
                status: status.restore(),
            },
            Self::AsyncTool { call_id, status } => NotificationSource::AsyncTool {
                call_id,
                status: status.restore(),
            },
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum CheckpointNotificationLevel {
    Info,
    Warning,
    Error,
}

impl CheckpointNotificationLevel {
    fn capture(level: &NotificationLevel) -> Self {
        match level {
            NotificationLevel::Info => Self::Info,
            NotificationLevel::Warning => Self::Warning,
            NotificationLevel::Error => Self::Error,
        }
    }

    fn restore(self) -> NotificationLevel {
        match self {
            Self::Info => NotificationLevel::Info,
            Self::Warning => NotificationLevel::Warning,
            Self::Error => NotificationLevel::Error,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) struct CheckpointNotification {
    id: String,
    source: CheckpointNotificationSource,
    level: CheckpointNotificationLevel,
    message: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    content: Vec<ContentBlock>,
}

impl CheckpointNotification {
    pub(in crate::core::checkpoint::v1) fn capture(notification: &Notification) -> Self {
        Self {
            id: notification.id.clone(),
            source: CheckpointNotificationSource::capture(&notification.source),
            level: CheckpointNotificationLevel::capture(&notification.level),
            message: notification.message.clone(),
            content: notification.content.clone(),
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> Notification {
        Notification {
            id: self.id,
            source: self.source.restore(),
            level: self.level.restore(),
            message: self.message,
            content: self.content,
        }
    }
}
