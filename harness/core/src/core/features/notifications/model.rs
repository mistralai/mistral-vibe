use serde::Deserialize;
use serde::Serialize;

use crate::core::wire::content::ContentBlock;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum NotificationSource {
    BackgroundProcess {
        process_id: String,
        status: ProcessTerminalStatus,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        exit_code: Option<i32>,
    },
    Subagent {
        agent_name: String,
        status: SubagentNotificationStatus,
    },
    AsyncTool {
        call_id: String,
        status: AsyncToolNotificationStatus,
    },
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ProcessTerminalStatus {
    Completed,
    Failed,
    Stopped,
    Orphaned,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SubagentNotificationStatus {
    Completed,
    Failed,
    Interrupted,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum AsyncToolNotificationStatus {
    Completed,
    Failed,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum NotificationLevel {
    Info,
    Warning,
    Error,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct Notification {
    pub id: String,
    pub source: NotificationSource,
    pub level: NotificationLevel,
    pub message: String,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub content: Vec<ContentBlock>,
}
