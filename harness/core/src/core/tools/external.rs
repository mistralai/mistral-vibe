use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;

use crate::core::hooks::{HookToolKey, HookToolTarget};

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum RuntimeBuiltinToolName {
    SelfSleep,
    FileSystemReadFile,
    FileSystemWriteFile,
    FileSystemSearchReplace,
    FileSystemBash,
    SkillRead,
    ProcessStart,
    ProcessOutput,
    ProcessWrite,
    ProcessList,
    ProcessStop,
    SubagentList,
    SubagentSpawn,
    SubagentWait,
    SubagentSendMessage,
    SubagentInterrupt,
    SubagentStop,
}

impl RuntimeBuiltinToolName {
    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::SelfSleep => "self.sleep",
            Self::FileSystemReadFile => "file_system.read_file",
            Self::FileSystemWriteFile => "file_system.write_file",
            Self::FileSystemSearchReplace => "file_system.search_replace",
            Self::FileSystemBash => "file_system.bash",
            Self::SkillRead => "skill.read",
            Self::ProcessStart => "process.start",
            Self::ProcessOutput => "process.output",
            Self::ProcessWrite => "process.write",
            Self::ProcessList => "process.list",
            Self::ProcessStop => "process.stop",
            Self::SubagentList => "subagent.list",
            Self::SubagentSpawn => "subagent.spawn",
            Self::SubagentWait => "subagent.wait",
            Self::SubagentSendMessage => "subagent.send_message",
            Self::SubagentInterrupt => "subagent.interrupt",
            Self::SubagentStop => "subagent.stop",
        }
    }

    fn from_str(value: &str) -> Option<Self> {
        match value {
            "self.sleep" => Some(Self::SelfSleep),
            "file_system.read_file" => Some(Self::FileSystemReadFile),
            "file_system.write_file" => Some(Self::FileSystemWriteFile),
            "file_system.search_replace" => Some(Self::FileSystemSearchReplace),
            "file_system.bash" => Some(Self::FileSystemBash),
            "skill.read" => Some(Self::SkillRead),
            "process.start" => Some(Self::ProcessStart),
            "process.output" => Some(Self::ProcessOutput),
            "process.write" => Some(Self::ProcessWrite),
            "process.list" => Some(Self::ProcessList),
            "process.stop" => Some(Self::ProcessStop),
            "subagent.list" => Some(Self::SubagentList),
            "subagent.spawn" => Some(Self::SubagentSpawn),
            "subagent.wait" => Some(Self::SubagentWait),
            "subagent.send_message" => Some(Self::SubagentSendMessage),
            "subagent.interrupt" => Some(Self::SubagentInterrupt),
            "subagent.stop" => Some(Self::SubagentStop),
            _ => None,
        }
    }

    pub(crate) fn is_skill_read(self) -> bool {
        matches!(self, Self::SkillRead)
    }

    fn direct_model_name(self, invocation_name: &str) -> Option<String> {
        match self {
            Self::FileSystemReadFile => Some("read_file".to_string()),
            Self::FileSystemWriteFile => Some("write_file".to_string()),
            Self::FileSystemSearchReplace => Some("edit".to_string()),
            Self::FileSystemBash => command_invocation_name(invocation_name),
            Self::SkillRead => Some("skill".to_string()),
            Self::SelfSleep
            | Self::ProcessStart
            | Self::ProcessOutput
            | Self::ProcessWrite
            | Self::ProcessList
            | Self::ProcessStop
            | Self::SubagentList
            | Self::SubagentSpawn
            | Self::SubagentWait
            | Self::SubagentSendMessage
            | Self::SubagentInterrupt
            | Self::SubagentStop => None,
        }
    }

    fn programmatic_name(self, invocation_name: &str) -> Option<String> {
        match self {
            Self::SelfSleep => Some("sleep".to_string()),
            Self::FileSystemReadFile => Some("read_file".to_string()),
            Self::FileSystemWriteFile => Some("write_file".to_string()),
            Self::FileSystemSearchReplace => Some("edit".to_string()),
            Self::FileSystemBash => command_invocation_name(invocation_name),
            Self::SkillRead => None,
            Self::ProcessStart => Some("process_start".to_string()),
            Self::ProcessOutput => Some("process_output".to_string()),
            Self::ProcessWrite => Some("process_write".to_string()),
            Self::ProcessList => Some("process_list".to_string()),
            Self::ProcessStop => Some("process_stop".to_string()),
            Self::SubagentList => Some("agent_list".to_string()),
            Self::SubagentSpawn => Some("agent_spawn".to_string()),
            Self::SubagentWait => Some("agent_wait".to_string()),
            Self::SubagentSendMessage => Some("agent_message".to_string()),
            Self::SubagentInterrupt => Some("agent_interrupt".to_string()),
            Self::SubagentStop => Some("agent_stop".to_string()),
        }
    }

    fn hook_tool_key(self) -> HookToolKey {
        let target = match self {
            Self::SelfSleep => HookToolTarget::SelfTool,
            Self::FileSystemReadFile
            | Self::FileSystemWriteFile
            | Self::FileSystemSearchReplace
            | Self::FileSystemBash => HookToolTarget::Filesystem,
            Self::SkillRead => HookToolTarget::Skill,
            Self::ProcessStart
            | Self::ProcessOutput
            | Self::ProcessWrite
            | Self::ProcessList
            | Self::ProcessStop => HookToolTarget::Process,
            Self::SubagentList
            | Self::SubagentSpawn
            | Self::SubagentWait
            | Self::SubagentSendMessage
            | Self::SubagentInterrupt
            | Self::SubagentStop => HookToolTarget::Subagent,
        };
        HookToolKey {
            target,
            qualified_name: self.as_str().to_string(),
        }
    }
}

fn command_invocation_name(name: &str) -> Option<String> {
    (name == "bash").then(|| name.to_string())
}

pub(crate) fn provided_tool_runtime_name(group_name: &str, tool_name: &str) -> String {
    format!("provided_tool::{group_name}::{tool_name}")
}

impl Serialize for RuntimeBuiltinToolName {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for RuntimeBuiltinToolName {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = String::deserialize(deserializer)?;
        Self::from_str(&value).ok_or_else(|| {
            D::Error::custom(format!("unknown runtime built-in tool name {value:?}"))
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ToolTarget {
    RuntimeBuiltin {
        name: RuntimeBuiltinToolName,
    },
    Provided {
        group_name: String,
        tool_name: String,
    },
}

impl ToolTarget {
    pub(crate) fn hook_tool_key(&self) -> HookToolKey {
        match self {
            Self::RuntimeBuiltin { name } => name.hook_tool_key(),
            Self::Provided {
                group_name,
                tool_name,
            } => HookToolKey {
                target: HookToolTarget::Provided,
                qualified_name: format!("{group_name}.{tool_name}"),
            },
        }
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ToolOrigin {
    TopLevel,
    Programmatic,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub(crate) enum ExternalTool {
    RuntimeBuiltin {
        name: RuntimeBuiltinToolName,
        #[serde(skip)]
        invocation_name: String,
        arguments: Value,
    },
    Provided {
        group_name: String,
        tool_name: String,
        arguments: Value,
    },
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct RuntimeBuiltinToolCall {
    #[serde(rename = "type")]
    kind: RuntimeBuiltinToolCallKind,
    pub(crate) name: RuntimeBuiltinToolName,
    pub(crate) arguments: Value,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum RuntimeBuiltinToolCallKind {
    RuntimeBuiltin,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ProvidedToolCall {
    #[serde(rename = "type")]
    kind: ProvidedToolCallKind,
    pub(crate) group_name: String,
    pub(crate) tool_name: String,
    pub(crate) arguments: Value,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum ProvidedToolCallKind {
    Provided,
}

impl ExternalTool {
    pub(crate) fn is_skill_read(&self) -> bool {
        matches!(
            self,
            Self::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::SkillRead,
                ..
            }
        )
    }

    pub(crate) fn from_target(
        target: ToolTarget,
        invocation_name: impl Into<String>,
        arguments: Value,
    ) -> Self {
        match target {
            ToolTarget::RuntimeBuiltin { name } => Self::RuntimeBuiltin {
                name,
                invocation_name: invocation_name.into(),
                arguments,
            },
            ToolTarget::Provided {
                group_name,
                tool_name,
            } => Self::Provided {
                group_name,
                tool_name,
                arguments,
            },
        }
    }

    pub(crate) fn with_arguments(&self, arguments: Value) -> Self {
        match self {
            Self::RuntimeBuiltin {
                name,
                invocation_name,
                ..
            } => Self::RuntimeBuiltin {
                name: *name,
                invocation_name: invocation_name.clone(),
                arguments,
            },
            Self::Provided {
                group_name,
                tool_name,
                ..
            } => Self::Provided {
                group_name: group_name.clone(),
                tool_name: tool_name.clone(),
                arguments,
            },
        }
    }

    pub(crate) fn direct_model_name(&self) -> Option<String> {
        match self {
            Self::RuntimeBuiltin {
                name,
                invocation_name,
                ..
            } => name.direct_model_name(invocation_name),
            Self::Provided { tool_name, .. } => Some(tool_name.clone()),
        }
    }

    pub(crate) fn programmatic_name(&self) -> Option<String> {
        match self {
            Self::RuntimeBuiltin {
                name,
                invocation_name,
                ..
            } => name.programmatic_name(invocation_name),
            Self::Provided {
                group_name,
                tool_name,
                ..
            } => Some(provided_tool_runtime_name(group_name, tool_name)),
        }
    }

    pub(crate) fn hook_tool_key(&self) -> HookToolKey {
        self.target().hook_tool_key()
    }

    fn target(&self) -> ToolTarget {
        match self {
            Self::RuntimeBuiltin { name, .. } => ToolTarget::RuntimeBuiltin { name: *name },
            Self::Provided {
                group_name,
                tool_name,
                ..
            } => ToolTarget::Provided {
                group_name: group_name.clone(),
                tool_name: tool_name.clone(),
            },
        }
    }

    pub(crate) fn runtime_builtin_call(&self) -> Option<RuntimeBuiltinToolCall> {
        let Self::RuntimeBuiltin {
            name, arguments, ..
        } = self
        else {
            return None;
        };
        Some(RuntimeBuiltinToolCall {
            kind: RuntimeBuiltinToolCallKind::RuntimeBuiltin,
            name: *name,
            arguments: arguments.clone(),
        })
    }

    pub(crate) fn provided_call(&self) -> Option<ProvidedToolCall> {
        let Self::Provided {
            group_name,
            tool_name,
            arguments,
        } = self
        else {
            return None;
        };
        Some(ProvidedToolCall {
            kind: ProvidedToolCallKind::Provided,
            group_name: group_name.clone(),
            tool_name: tool_name.clone(),
            arguments: arguments.clone(),
        })
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct ExternalToolCall {
    pub(crate) action_id: String,
    pub(crate) call_id: String,
    pub(crate) origin: ToolOrigin,
    pub(crate) call: ExternalTool,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
pub(crate) struct HookToolCall {
    pub(crate) action_id: String,
    pub(crate) call_id: String,
    pub(crate) call: ExternalTool,
}

impl From<&ExternalToolCall> for HookToolCall {
    fn from(call: &ExternalToolCall) -> Self {
        Self {
            action_id: call.action_id.clone(),
            call_id: call.call_id.clone(),
            call: call.call.clone(),
        }
    }
}

impl ExternalToolCall {
    pub(crate) fn hook_tool_key(&self) -> HookToolKey {
        self.call.hook_tool_key()
    }
}

pub(crate) fn effect_id_for_operation(origin: ToolOrigin, operation_id: &str) -> String {
    let namespace = match origin {
        ToolOrigin::TopLevel => "top_level",
        ToolOrigin::Programmatic => "programmatic",
    };
    crate::core::action_id::tool(namespace, operation_id)
}
