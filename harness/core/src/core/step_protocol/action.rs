use serde::Deserialize;
use serde::Serialize;
use serde_json::Value;

use crate::core::error::CoreError;
use crate::core::features::compaction::CompactionTrigger;
use crate::core::hooks::HookCall;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, ProvidedToolCall, RuntimeBuiltinToolCall,
};
use crate::core::wire::message::Message;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum CompletionPurpose {
    Agent,
    Compaction,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "purpose", rename_all = "snake_case")]
pub(crate) enum CompletionActionKind {
    Agent,
    Compaction {
        compaction_id: String,
        trigger: CompactionTrigger,
        attempt: u32,
    },
}

impl CompletionActionKind {
    pub(crate) fn purpose(&self) -> CompletionPurpose {
        match self {
            Self::Agent => CompletionPurpose::Agent,
            Self::Compaction { .. } => CompletionPurpose::Compaction,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ToolDefinition {
    pub name: String,
    pub description: String,
    pub parameters: Value,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ModelMessageUpdate {
    Append {
        base_revision: u64,
        revision: u64,
        messages: Vec<Message>,
    },
    Replace {
        revision: u64,
        messages: Vec<Message>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ModelToolCatalogUpdate {
    Keep {
        revision: u64,
    },
    Replace {
        revision: u64,
        tools: Vec<ToolDefinition>,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ModelInputUpdate {
    pub messages: ModelMessageUpdate,
    pub tool_catalog: ModelToolCatalogUpdate,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum FilesystemOperation {
    Write {
        workspace_path: String,
        content: String,
    },
}

#[allow(clippy::large_enum_variant)]
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum Action {
    #[serde(rename = "llm_call")]
    Completion {
        #[serde(rename = "action_id")]
        effect_id: String,
        turn_id: Option<String>,
        #[serde(flatten)]
        kind: CompletionActionKind,
        iteration: u32,
        max_iterations: Option<u32>,
        model_input: ModelInputUpdate,
    },
    #[serde(rename = "runtime_builtin_tool_call")]
    RuntimeBuiltinTool {
        #[serde(rename = "action_id")]
        effect_id: String,
        turn_id: String,
        #[serde(rename = "call_id")]
        operation_id: String,
        call: RuntimeBuiltinToolCall,
    },
    #[serde(rename = "provided_tool_call")]
    ProvidedTool {
        #[serde(rename = "action_id")]
        effect_id: String,
        turn_id: String,
        #[serde(rename = "call_id")]
        operation_id: String,
        call: ProvidedToolCall,
    },
    #[serde(rename = "hook_call")]
    Hook {
        #[serde(rename = "action_id")]
        effect_id: String,
        turn_id: String,
        hook_binding_ids: Vec<String>,
        #[serde(flatten)]
        call: HookCall,
    },
    Filesystem {
        #[serde(rename = "action_id")]
        effect_id: String,
        turn_id: String,
        operation: FilesystemOperation,
    },
}

impl Action {
    pub(crate) fn action_id(&self) -> &str {
        match self {
            Self::Completion { effect_id, .. }
            | Self::RuntimeBuiltinTool { effect_id, .. }
            | Self::ProvidedTool { effect_id, .. }
            | Self::Hook { effect_id, .. }
            | Self::Filesystem { effect_id, .. } => effect_id,
        }
    }

    pub(crate) fn external_tool(call: &ExternalToolCall, turn_id: &str) -> Self {
        match &call.call {
            ExternalTool::RuntimeBuiltin { .. } => Self::RuntimeBuiltinTool {
                effect_id: call.action_id.clone(),
                turn_id: turn_id.to_string(),
                operation_id: call.call_id.clone(),
                call: call
                    .call
                    .runtime_builtin_call()
                    .expect("matched Runtime built-in call"),
            },
            ExternalTool::Provided { .. } => Self::ProvidedTool {
                effect_id: call.action_id.clone(),
                turn_id: turn_id.to_string(),
                operation_id: call.call_id.clone(),
                call: call.call.provided_call().expect("matched provided call"),
            },
        }
    }

    pub(crate) fn hook(
        effect_id: String,
        turn_id: &str,
        hook_binding_ids: Vec<String>,
        call: HookCall,
    ) -> Result<Self, CoreError> {
        if hook_binding_ids.is_empty() {
            return Err(CoreError::invariant(
                "cannot emit a hook action without matching bindings",
            ));
        }
        Ok(Self::Hook {
            effect_id,
            turn_id: turn_id.to_string(),
            hook_binding_ids,
            call,
        })
    }

    pub(crate) fn filesystem_write(
        effect_id: String,
        turn_id: &str,
        workspace_path: String,
        content: String,
    ) -> Self {
        Self::Filesystem {
            effect_id,
            turn_id: turn_id.to_string(),
            operation: FilesystemOperation::Write {
                workspace_path,
                content,
            },
        }
    }
}
