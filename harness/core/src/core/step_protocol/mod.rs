use std::collections::HashSet;

mod action;
mod command_state;
mod determinism;
mod observation;

pub(crate) use action::{
    Action, CompletionActionKind, CompletionPurpose, FilesystemOperation, ModelInputUpdate,
    ModelMessageUpdate, ModelToolCatalogUpdate, ToolDefinition,
};
pub(crate) use command_state::CommandState;
pub(crate) use determinism::DeterminismContext;
#[cfg(test)]
pub(crate) use observation::AcceptedToolResult;
pub(crate) use observation::{
    ActionAbandonedCause, CandidateDiscardCause, Observation, Outcome, ToolDiscoveryKind,
    ToolDiscoverySummary, Transition, TurnCompletion, TurnStopReason,
};

use crate::core::capabilities::HarnessCapabilitySet;
use crate::core::capabilities::PluginContextDefinition;
use crate::core::config::{HarnessConfigUpdate, HarnessSettings};
use crate::core::features::compaction::CompactionTrigger;
use crate::core::features::notifications::Notification;
use crate::core::hooks::{HookPoint, HookResult};
use crate::core::wire::completion::CompletionResult;
use crate::core::wire::content::ContentBlock;
use crate::core::wire::tool::{ProtocolError, ToolResult};
use crate::core::wire::user_message::UserMessageMode;
use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

pub(crate) const STEP_PROTOCOL_VERSION: u8 = 1;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct HarnessInput {
    pub(super) protocol_version: u8,
    pub(super) input_id: u64,
    pub(super) determinism: DeterminismContext,
    pub(super) command: HarnessCommand,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct SessionTransition {
    pub(super) protocol_version: u8,
    pub(super) input_id: u64,
    pub(super) next: HarnessNextAction,
    pub(super) observations: Vec<Observation>,
    pub(super) turn: Turn,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HarnessActionDirective {
    Dispatch { action: Action },
    Keep { action_id: String },
    Refresh { action: Action },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HarnessNextAction {
    None,
    Actions {
        directives: Vec<HarnessActionDirective>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HarnessApplyResult {
    Accepted { transition: Box<SessionTransition> },
    Rejected { rejection: HarnessCommandRejection },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "code", rename_all = "snake_case")]
pub(crate) enum HarnessCommandRejection {
    StaleInput {
        received_input_id: u64,
        last_accepted_input_id: u64,
    },
    OutOfOrderInput {
        received_input_id: u64,
        expected_input_id: u64,
    },
    InputConflict {
        input_id: u64,
    },
    InvalidState {
        command_type: String,
        state: CommandState,
    },
    InvalidCorrelation {
        received_action_id: String,
        pending_action_ids: Vec<String>,
    },
    InvalidCommand {
        error: Box<ProtocolError>,
    },
}

pub(crate) fn invalid_command(message: impl Into<String>) -> HarnessCommandRejection {
    HarnessCommandRejection::InvalidCommand {
        error: Box::new(ProtocolError {
            code: "invalid_command".to_string(),
            message: message.into(),
            retryable: false,
            details: Value::Null,
        }),
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
pub(crate) struct Inspection {
    pub(super) protocol_version: u8,
    pub(super) status: SessionStatus,
    pub(super) active_turn_id: Option<String>,
    pub(super) last_turn_id: Option<String>,
    pub(super) pending_actions: Vec<PendingActionInspection>,
    pub(super) message_count: usize,
    pub(super) context_revision: u64,
    pub(super) tool_catalog_revision: u64,
    pub(super) last_input_id: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HarnessConfigurationChange {
    SystemInstructions { value: String },
    Settings { value: HarnessSettings },
    Capabilities { value: HarnessCapabilitySet },
    SkillCatalogFingerprint { value: String },
    Plugins { value: Vec<PluginContextDefinition> },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum HarnessCommand {
    UserMessage {
        turn_id: String,
        content: Vec<ContentBlock>,
        mode: UserMessageMode,
    },
    ContextMessage {
        content: Vec<ContentBlock>,
    },
    Notification {
        notification: Notification,
    },
    Compact {
        extra_instructions: String,
    },
    Reconfigure {
        changes: Vec<HarnessConfigurationChange>,
    },
    CompletionModelInputResyncRequested {
        action_id: String,
    },
    CompletionSucceeded {
        action_id: String,
        result: CompletionResult,
    },
    CompletionFailed {
        action_id: String,
        error: ProtocolError,
    },
    HookCompleted {
        action_id: String,
        result: HookResult,
    },
    HookFailed {
        action_id: String,
        error: ProtocolError,
    },
    ToolSucceeded {
        action_id: String,
        call_id: String,
        result: ToolResult,
    },
    ToolFailed {
        action_id: String,
        call_id: String,
        result: ToolResult,
    },
    FilesystemSucceeded {
        action_id: String,
        result: FilesystemResult,
    },
    FilesystemFailed {
        action_id: String,
        error: ProtocolError,
    },
    FailTurn {
        expected_turn_id: String,
        action_id: String,
        error: ProtocolError,
    },
    Interrupt {
        expected_turn_id: String,
        #[serde(default)]
        reason: Option<String>,
    },
}

impl HarnessCommand {
    pub(crate) fn command_type(&self) -> &'static str {
        match self {
            Self::UserMessage { .. } => "user_message",
            Self::ContextMessage { .. } => "context_message",
            Self::Notification { .. } => "notification",
            Self::Compact { .. } => "compact",
            Self::Reconfigure { .. } => "reconfigure",
            Self::CompletionModelInputResyncRequested { .. } => {
                "completion_model_input_resync_requested"
            }
            Self::CompletionSucceeded { .. } => "completion_succeeded",
            Self::CompletionFailed { .. } => "completion_failed",
            Self::HookCompleted { .. } => "hook_completed",
            Self::HookFailed { .. } => "hook_failed",
            Self::ToolSucceeded { .. } => "tool_succeeded",
            Self::ToolFailed { .. } => "tool_failed",
            Self::FilesystemSucceeded { .. } => "filesystem_succeeded",
            Self::FilesystemFailed { .. } => "filesystem_failed",
            Self::FailTurn { .. } => "fail_turn",
            Self::Interrupt { .. } => "interrupt",
        }
    }

    pub(crate) fn correlated_action_id(&self) -> Option<&str> {
        match self {
            Self::CompletionModelInputResyncRequested { action_id }
            | Self::CompletionSucceeded { action_id, .. }
            | Self::CompletionFailed { action_id, .. }
            | Self::HookCompleted { action_id, .. }
            | Self::HookFailed { action_id, .. }
            | Self::ToolSucceeded { action_id, .. }
            | Self::ToolFailed { action_id, .. }
            | Self::FilesystemSucceeded { action_id, .. }
            | Self::FilesystemFailed { action_id, .. }
            | Self::FailTurn { action_id, .. } => Some(action_id),
            Self::UserMessage { .. }
            | Self::ContextMessage { .. }
            | Self::Notification { .. }
            | Self::Compact { .. }
            | Self::Reconfigure { .. }
            | Self::Interrupt { .. } => None,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum FilesystemResult {
    Write { model_path: String },
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum SessionStatus {
    Idle,
    Running,
    Compacting,
    Completed,
    Failed,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum PendingCompletionPurpose {
    Agent,
    Compaction,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum PendingActionInspection {
    Completion {
        purpose: PendingCompletionPurpose,
        action_id: String,
    },
    RuntimeBuiltinToolCall {
        action_id: String,
        call_id: String,
        name: crate::core::tools::external::RuntimeBuiltinToolName,
    },
    ProvidedToolCall {
        action_id: String,
        call_id: String,
        group_name: String,
        tool_name: String,
    },
    HookCall {
        action_id: String,
        hook: HookPoint,
        hook_binding_ids: Vec<String>,
    },
    Filesystem {
        action_id: String,
        operation: PendingFilesystemOperationInspection,
    },
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum PendingFilesystemOperationInspection {
    Write { workspace_path: String },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "status", rename_all = "snake_case")]
pub(crate) enum Turn {
    Idle,
    Compacting {
        trigger: CompactionTrigger,
    },
    Running {
        turn_id: String,
    },
    Completed {
        turn_id: String,
        output: Vec<ContentBlock>,
    },
    Interrupted {
        turn_id: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        reason: Option<String>,
    },
    Failed {
        turn_id: String,
        error: ProtocolError,
    },
}

pub(crate) fn configuration_update(
    changes: Vec<HarnessConfigurationChange>,
) -> Result<HarnessConfigUpdate, String> {
    if changes.is_empty() {
        return Err("reconfigure changes must not be empty".to_string());
    }
    let mut update = HarnessConfigUpdate::default();
    let mut seen = HashSet::new();
    for change in changes {
        let kind = match &change {
            HarnessConfigurationChange::SystemInstructions { .. } => "system_instructions",
            HarnessConfigurationChange::Settings { .. } => "settings",
            HarnessConfigurationChange::Capabilities { .. } => "capabilities",
            HarnessConfigurationChange::SkillCatalogFingerprint { .. } => {
                "skill_catalog_fingerprint"
            }
            HarnessConfigurationChange::Plugins { .. } => "plugins",
        };
        if !seen.insert(kind) {
            return Err(format!("duplicate reconfigure change {kind:?}"));
        }
        match change {
            HarnessConfigurationChange::SystemInstructions { value } => {
                update.system_instructions = Some(value);
            }
            HarnessConfigurationChange::Settings { value } => update.settings = Some(value),
            HarnessConfigurationChange::Capabilities { value } => {
                update.capabilities = Some(value);
            }
            HarnessConfigurationChange::SkillCatalogFingerprint { value } => {
                update.skill_catalog_fingerprint = Some(value);
            }
            HarnessConfigurationChange::Plugins { value } => update.plugins = Some(value),
        }
    }
    Ok(update)
}
