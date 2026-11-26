use super::tools::ToolName;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Mode {
    Disabled,
    Enabled {
        #[serde(default = "default_operations")]
        operations: Vec<ToolName>,
        #[serde(default)]
        conversation_forking: bool,
        #[serde(default, skip_serializing_if = "IdleMessageStarts::is_direct_children")]
        idle_message_starts: IdleMessageStarts,
    },
}

/// Whether a parent's message to an idle child starts a new Turn in that child.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum IdleMessageStarts {
    #[default]
    DirectChildren,
    #[serde(rename = "none")]
    Never,
}

impl IdleMessageStarts {
    fn is_direct_children(&self) -> bool {
        matches!(self, Self::DirectChildren)
    }
}

fn default_operations() -> Vec<ToolName> {
    ToolName::ALL.to_vec()
}

impl Mode {
    pub(crate) fn enabled() -> Self {
        Self::Enabled {
            operations: default_operations(),
            conversation_forking: false,
            idle_message_starts: IdleMessageStarts::DirectChildren,
        }
    }
    pub(crate) fn with_conversation_forking(mut self, enabled: bool) -> Self {
        if let Self::Enabled {
            conversation_forking,
            ..
        } = &mut self
        {
            *conversation_forking = enabled;
        }
        self
    }
    pub(crate) fn is_enabled(&self) -> bool {
        matches!(self, Self::Enabled { .. })
    }
    pub(crate) fn supports(&self, operation: ToolName) -> bool {
        matches!(self, Self::Enabled { operations, .. } if operations.contains(&operation))
    }
    /// Whether the agent can create children, so its guidance may name child operations.
    pub(crate) fn can_spawn(&self) -> bool {
        self.supports(ToolName::Spawn)
    }
    pub(crate) fn can_list_configs(&self) -> bool {
        self.supports(ToolName::ListConfigs)
    }
    pub(crate) fn supports_conversation_forking(&self) -> bool {
        matches!(
            self,
            Self::Enabled {
                conversation_forking: true,
                ..
            }
        )
    }
    /// Whether the agent can resume an idle child by sending it a message.
    pub(crate) fn messages_resume_idle_children(&self) -> bool {
        matches!(
            self,
            Self::Enabled {
                operations,
                idle_message_starts: IdleMessageStarts::DirectChildren,
                ..
            } if operations.contains(&ToolName::SendMessage)
        )
    }
    pub(crate) fn changes_system_prompt(&self, next: &Self) -> bool {
        self.without_idle_message_starts() != next.without_idle_message_starts()
    }
    /// The Runtime's tree policy owns `idle_message_starts`; it only changes result text.
    fn without_idle_message_starts(&self) -> Self {
        match self {
            Self::Disabled => Self::Disabled,
            Self::Enabled {
                operations,
                conversation_forking,
                ..
            } => Self::Enabled {
                operations: operations.clone(),
                conversation_forking: *conversation_forking,
                idle_message_starts: IdleMessageStarts::DirectChildren,
            },
        }
    }
}

pub(crate) fn validate_reconfiguration(current: &Mode, next: &Mode) -> Result<(), &'static str> {
    if current.without_idle_message_starts() != next.without_idle_message_starts() {
        return Err("settings.tools.subagents cannot be reconfigured after session creation");
    }
    Ok(())
}
