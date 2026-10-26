use serde::de::Error as _;
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use serde_json::Value;

use crate::core::hooks::{HookToolKey, HookToolTarget};

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum RuntimeBuiltinToolName {
    SelfWait,
    SelfSleep,
    FileSystemReadFile,
    FileSystemWriteFile,
    FileSystemEdit,
    FileSystemBash,
    SkillRead,
    ProcessStart,
    ProcessOutput,
    ProcessWrite,
    ProcessList,
    ProcessStop,
    SubagentListConfigs,
    SubagentList,
    SubagentSpawn,
    /// Deprecated: `subagent.wait` is replay-only; use the standalone `wait` tool.
    SubagentWait,
    SubagentSendMessage,
    SubagentInterrupt,
    /// Deprecated: `subagent.stop` is replay-only and has no replacement.
    SubagentStop,
    /// Deprecated: `subagent.list` is replay-only; use `agents.list`.
    DeprecatedSubagentList,
    /// Deprecated: `subagent.spawn` is replay-only; use `agents.spawn`.
    DeprecatedSubagentSpawn,
    /// Deprecated: `subagent.send_message` is replay-only; use `agents.send_message`.
    DeprecatedSubagentSendMessage,
    /// Deprecated: `subagent.interrupt` is replay-only; use `agents.interrupt`.
    DeprecatedSubagentInterrupt,
    /// Deprecated: `file_system.search_replace` is replay-only; use `file_system.edit`.
    DeprecatedFileSystemSearchReplace,
}

impl RuntimeBuiltinToolName {
    /// Deprecated: retired names that restore from older Sessions and fail
    /// without executing; their replacements are the `agents.*` tools and
    /// `file_system.edit`.
    pub(crate) const DEPRECATED: [Self; 7] = [
        Self::DeprecatedSubagentList,
        Self::DeprecatedSubagentSpawn,
        Self::DeprecatedSubagentSendMessage,
        Self::DeprecatedSubagentInterrupt,
        Self::SubagentWait,
        Self::SubagentStop,
        Self::DeprecatedFileSystemSearchReplace,
    ];

    pub(crate) fn as_str(self) -> &'static str {
        match self {
            Self::SelfWait => "self.wait",
            Self::SelfSleep => "self.sleep",
            Self::FileSystemReadFile => "file_system.read_file",
            Self::FileSystemWriteFile => "file_system.write_file",
            Self::FileSystemEdit => "file_system.edit",
            Self::FileSystemBash => "file_system.bash",
            Self::SkillRead => "skill.read",
            Self::ProcessStart => "process.start",
            Self::ProcessOutput => "process.output",
            Self::ProcessWrite => "process.write",
            Self::ProcessList => "process.list",
            Self::ProcessStop => "process.stop",
            Self::SubagentListConfigs => "agents.list_configs",
            Self::SubagentList => "agents.list",
            Self::SubagentSpawn => "agents.spawn",
            Self::SubagentWait => "subagent.wait",
            Self::SubagentSendMessage => "agents.send_message",
            Self::SubagentInterrupt => "agents.interrupt",
            Self::SubagentStop => "subagent.stop",
            Self::DeprecatedSubagentList => "subagent.list",
            Self::DeprecatedSubagentSpawn => "subagent.spawn",
            Self::DeprecatedSubagentSendMessage => "subagent.send_message",
            Self::DeprecatedSubagentInterrupt => "subagent.interrupt",
            Self::DeprecatedFileSystemSearchReplace => "file_system.search_replace",
        }
    }

    fn from_str(value: &str) -> Option<Self> {
        match value {
            "self.wait" => Some(Self::SelfWait),
            "self.sleep" => Some(Self::SelfSleep),
            "file_system.read_file" => Some(Self::FileSystemReadFile),
            "file_system.write_file" => Some(Self::FileSystemWriteFile),
            "file_system.edit" => Some(Self::FileSystemEdit),
            "file_system.bash" => Some(Self::FileSystemBash),
            "skill.read" => Some(Self::SkillRead),
            "process.start" => Some(Self::ProcessStart),
            "process.output" => Some(Self::ProcessOutput),
            "process.write" => Some(Self::ProcessWrite),
            "process.list" => Some(Self::ProcessList),
            "process.stop" => Some(Self::ProcessStop),
            "agents.list_configs" => Some(Self::SubagentListConfigs),
            "agents.list" => Some(Self::SubagentList),
            "agents.spawn" => Some(Self::SubagentSpawn),
            "subagent.wait" => Some(Self::SubagentWait),
            "agents.send_message" => Some(Self::SubagentSendMessage),
            "agents.interrupt" => Some(Self::SubagentInterrupt),
            "subagent.stop" => Some(Self::SubagentStop),
            "subagent.list" => Some(Self::DeprecatedSubagentList),
            "subagent.spawn" => Some(Self::DeprecatedSubagentSpawn),
            "subagent.send_message" => Some(Self::DeprecatedSubagentSendMessage),
            "subagent.interrupt" => Some(Self::DeprecatedSubagentInterrupt),
            "file_system.search_replace" => Some(Self::DeprecatedFileSystemSearchReplace),
            _ => None,
        }
    }

    pub(crate) fn is_skill_read(self) -> bool {
        matches!(self, Self::SkillRead)
    }

    /// The tool's own sentence for a JSON Schema error, when the schema's text
    /// would not tell the model what to send, such as a failed `pattern`.
    /// `None` keeps the schema's text.
    pub(crate) fn explain_schema_error(
        self,
        error: &jsonschema::ValidationError<'_>,
    ) -> Option<String> {
        match self {
            Self::SubagentSpawn => {
                crate::core::features::subagents::explain_spawn_schema_error(error)
            }
            Self::ProcessWrite => {
                crate::core::features::background_processes::explain_write_schema_error(error)
            }
            _ => None,
        }
    }

    /// Rules the tool's JSON Schema cannot state, checked once it passed.
    pub(crate) fn check_domain(self, arguments: &Value) -> Result<(), String> {
        match self {
            Self::SubagentSpawn => crate::core::features::subagents::check_spawn_domain(arguments),
            _ => Ok(()),
        }
    }

    /// Whether the permission policy evaluates calls to this tool.
    ///
    /// Pure orchestration tools only coordinate the agent tree or pause the
    /// agent: they act on nothing outside the Session tree, so asking a user to
    /// approve them adds prompts that guard nothing. Starting an agent is not
    /// pure orchestration, because the new agent can act.
    pub(crate) fn is_permission_checked(self) -> bool {
        match self {
            Self::SelfWait
            | Self::SelfSleep
            | Self::SubagentList
            | Self::SubagentListConfigs
            | Self::SubagentSendMessage
            | Self::SubagentInterrupt
            | Self::SubagentWait
            | Self::SubagentStop
            | Self::DeprecatedSubagentList
            | Self::DeprecatedSubagentSendMessage
            | Self::DeprecatedSubagentInterrupt => false,
            Self::FileSystemReadFile
            | Self::FileSystemWriteFile
            | Self::FileSystemEdit
            | Self::DeprecatedFileSystemSearchReplace
            | Self::FileSystemBash
            | Self::SkillRead
            | Self::ProcessStart
            | Self::ProcessOutput
            | Self::ProcessWrite
            | Self::ProcessList
            | Self::ProcessStop
            | Self::SubagentSpawn
            | Self::DeprecatedSubagentSpawn => true,
        }
    }

    fn direct_model_name(self, invocation_name: &str) -> Option<String> {
        match self {
            Self::SelfWait => Some("wait".to_string()),
            Self::FileSystemReadFile => Some("read_file".to_string()),
            Self::FileSystemWriteFile => Some("write_file".to_string()),
            Self::FileSystemEdit | Self::DeprecatedFileSystemSearchReplace => {
                Some("edit".to_string())
            }
            Self::FileSystemBash => command_invocation_name(invocation_name),
            Self::SkillRead => Some("skill".to_string()),
            Self::SelfSleep
            | Self::ProcessStart
            | Self::ProcessOutput
            | Self::ProcessWrite
            | Self::ProcessList
            | Self::ProcessStop
            | Self::SubagentList
            | Self::SubagentListConfigs
            | Self::SubagentSpawn
            | Self::SubagentWait
            | Self::SubagentSendMessage
            | Self::SubagentInterrupt
            | Self::SubagentStop
            | Self::DeprecatedSubagentList
            | Self::DeprecatedSubagentSpawn
            | Self::DeprecatedSubagentSendMessage
            | Self::DeprecatedSubagentInterrupt => None,
        }
    }

    fn programmatic_runtime_name(self, invocation_name: &str) -> Option<String> {
        match self {
            Self::SelfWait => None,
            Self::SelfSleep => Some("sleep".to_string()),
            Self::FileSystemReadFile => Some("read_file".to_string()),
            Self::FileSystemWriteFile => Some("write_file".to_string()),
            Self::FileSystemEdit | Self::DeprecatedFileSystemSearchReplace => {
                Some("edit".to_string())
            }
            Self::FileSystemBash => command_invocation_name(invocation_name),
            Self::SkillRead => None,
            Self::ProcessStart => Some("process_start".to_string()),
            Self::ProcessOutput => Some("process_output".to_string()),
            Self::ProcessWrite => Some("process_write".to_string()),
            Self::ProcessList => Some("process_list".to_string()),
            Self::ProcessStop => Some("process_stop".to_string()),
            Self::SubagentList => Some("agents_list".to_string()),
            Self::SubagentListConfigs => Some("agents_list_configs".to_string()),
            Self::SubagentSpawn => Some("agents_spawn".to_string()),
            Self::SubagentWait => Some("agent_wait".to_string()),
            Self::SubagentSendMessage => Some("agents_send_message".to_string()),
            Self::SubagentInterrupt => Some("agents_interrupt".to_string()),
            Self::SubagentStop => Some("agent_stop".to_string()),
            Self::DeprecatedSubagentList => Some("agent_list".to_string()),
            Self::DeprecatedSubagentSpawn => Some("agent_spawn".to_string()),
            Self::DeprecatedSubagentSendMessage => Some("agent_message".to_string()),
            Self::DeprecatedSubagentInterrupt => Some("agent_interrupt".to_string()),
        }
    }

    fn hook_tool_key(self) -> HookToolKey {
        let target = match self {
            Self::SelfSleep | Self::SelfWait => HookToolTarget::SelfTool,
            Self::FileSystemReadFile
            | Self::FileSystemWriteFile
            | Self::FileSystemEdit
            | Self::DeprecatedFileSystemSearchReplace
            | Self::FileSystemBash => HookToolTarget::Filesystem,
            Self::SkillRead => HookToolTarget::Skill,
            Self::ProcessStart
            | Self::ProcessOutput
            | Self::ProcessWrite
            | Self::ProcessList
            | Self::ProcessStop => HookToolTarget::Process,
            Self::SubagentList
            | Self::SubagentListConfigs
            | Self::SubagentSpawn
            | Self::SubagentWait
            | Self::SubagentSendMessage
            | Self::SubagentInterrupt
            | Self::SubagentStop
            | Self::DeprecatedSubagentList
            | Self::DeprecatedSubagentSpawn
            | Self::DeprecatedSubagentSendMessage
            | Self::DeprecatedSubagentInterrupt => HookToolTarget::Subagent,
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

pub(crate) fn provided_tool_qualified_name(namespace: &str, tool_name: &str) -> String {
    format!("{namespace}.{tool_name}")
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
                qualified_name: provided_tool_qualified_name(group_name, tool_name),
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

    pub(crate) fn is_permission_checked(&self) -> bool {
        match self {
            Self::RuntimeBuiltin { name, .. } => name.is_permission_checked(),
            Self::Provided { .. } => true,
        }
    }

    pub(crate) fn is_file_read(&self) -> bool {
        matches!(
            self,
            Self::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::FileSystemReadFile,
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

    pub(crate) fn arguments(&self) -> &Value {
        match self {
            Self::RuntimeBuiltin { arguments, .. } | Self::Provided { arguments, .. } => arguments,
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

    pub(crate) fn programmatic_runtime_name(&self) -> Option<String> {
        match self {
            Self::RuntimeBuiltin {
                name,
                invocation_name,
                ..
            } => name.programmatic_runtime_name(invocation_name),
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
