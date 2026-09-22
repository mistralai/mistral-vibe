use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::features::programmatic_tool_calling::ProgramFunctionRecord;
use crate::core::tools::external::{ExternalTool, RuntimeBuiltinToolName};

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq)]
pub(in crate::core::checkpoint::v1) enum CheckpointRuntimeBuiltinToolName {
    #[serde(rename = "self.sleep")]
    SelfSleep,
    #[serde(rename = "file_system.read_file")]
    FileSystemReadFile,
    #[serde(rename = "file_system.write_file")]
    FileSystemWriteFile,
    #[serde(rename = "file_system.search_replace")]
    FileSystemSearchReplace,
    #[serde(rename = "file_system.bash")]
    FileSystemBash,
    #[serde(rename = "skill.read")]
    SkillRead,
    #[serde(rename = "process.start")]
    ProcessStart,
    #[serde(rename = "process.output")]
    ProcessOutput,
    #[serde(rename = "process.write")]
    ProcessWrite,
    #[serde(rename = "process.list")]
    ProcessList,
    #[serde(rename = "process.stop")]
    ProcessStop,
    #[serde(rename = "subagent.list")]
    SubagentList,
    #[serde(rename = "subagent.spawn")]
    SubagentSpawn,
    #[serde(rename = "subagent.wait")]
    SubagentWait,
    #[serde(rename = "subagent.send_message")]
    SubagentSendMessage,
    #[serde(rename = "subagent.interrupt")]
    SubagentInterrupt,
    #[serde(rename = "subagent.stop")]
    SubagentStop,
}

impl CheckpointRuntimeBuiltinToolName {
    fn capture(name: &RuntimeBuiltinToolName) -> Self {
        match name {
            RuntimeBuiltinToolName::SelfSleep => Self::SelfSleep,
            RuntimeBuiltinToolName::FileSystemReadFile => Self::FileSystemReadFile,
            RuntimeBuiltinToolName::FileSystemWriteFile => Self::FileSystemWriteFile,
            RuntimeBuiltinToolName::FileSystemSearchReplace => Self::FileSystemSearchReplace,
            RuntimeBuiltinToolName::FileSystemBash => Self::FileSystemBash,
            RuntimeBuiltinToolName::SkillRead => Self::SkillRead,
            RuntimeBuiltinToolName::ProcessStart => Self::ProcessStart,
            RuntimeBuiltinToolName::ProcessOutput => Self::ProcessOutput,
            RuntimeBuiltinToolName::ProcessWrite => Self::ProcessWrite,
            RuntimeBuiltinToolName::ProcessList => Self::ProcessList,
            RuntimeBuiltinToolName::ProcessStop => Self::ProcessStop,
            RuntimeBuiltinToolName::SubagentList => Self::SubagentList,
            RuntimeBuiltinToolName::SubagentSpawn => Self::SubagentSpawn,
            RuntimeBuiltinToolName::SubagentWait => Self::SubagentWait,
            RuntimeBuiltinToolName::SubagentSendMessage => Self::SubagentSendMessage,
            RuntimeBuiltinToolName::SubagentInterrupt => Self::SubagentInterrupt,
            RuntimeBuiltinToolName::SubagentStop => Self::SubagentStop,
        }
    }

    fn restore(self) -> RuntimeBuiltinToolName {
        match self {
            Self::SelfSleep => RuntimeBuiltinToolName::SelfSleep,
            Self::FileSystemReadFile => RuntimeBuiltinToolName::FileSystemReadFile,
            Self::FileSystemWriteFile => RuntimeBuiltinToolName::FileSystemWriteFile,
            Self::FileSystemSearchReplace => RuntimeBuiltinToolName::FileSystemSearchReplace,
            Self::FileSystemBash => RuntimeBuiltinToolName::FileSystemBash,
            Self::SkillRead => RuntimeBuiltinToolName::SkillRead,
            Self::ProcessStart => RuntimeBuiltinToolName::ProcessStart,
            Self::ProcessOutput => RuntimeBuiltinToolName::ProcessOutput,
            Self::ProcessWrite => RuntimeBuiltinToolName::ProcessWrite,
            Self::ProcessList => RuntimeBuiltinToolName::ProcessList,
            Self::ProcessStop => RuntimeBuiltinToolName::ProcessStop,
            Self::SubagentList => RuntimeBuiltinToolName::SubagentList,
            Self::SubagentSpawn => RuntimeBuiltinToolName::SubagentSpawn,
            Self::SubagentWait => RuntimeBuiltinToolName::SubagentWait,
            Self::SubagentSendMessage => RuntimeBuiltinToolName::SubagentSendMessage,
            Self::SubagentInterrupt => RuntimeBuiltinToolName::SubagentInterrupt,
            Self::SubagentStop => RuntimeBuiltinToolName::SubagentStop,
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) enum CheckpointExternalTool {
    RuntimeBuiltin {
        name: CheckpointRuntimeBuiltinToolName,
        arguments: Value,
    },
    Provided {
        group_name: String,
        tool_name: String,
        arguments: Value,
    },
}

impl CheckpointExternalTool {
    pub(in crate::core::checkpoint::v1) fn capture(tool: &ExternalTool) -> Self {
        match tool {
            ExternalTool::RuntimeBuiltin {
                name, arguments, ..
            } => Self::RuntimeBuiltin {
                name: CheckpointRuntimeBuiltinToolName::capture(name),
                arguments: arguments.clone(),
            },
            ExternalTool::Provided {
                group_name,
                tool_name,
                arguments,
            } => Self::Provided {
                group_name: group_name.clone(),
                tool_name: tool_name.clone(),
                arguments: arguments.clone(),
            },
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self, invocation_name: String) -> ExternalTool {
        match self {
            Self::RuntimeBuiltin { name, arguments } => ExternalTool::RuntimeBuiltin {
                name: name.restore(),
                invocation_name,
                arguments,
            },
            Self::Provided {
                group_name,
                tool_name,
                arguments,
            } => ExternalTool::Provided {
                group_name,
                tool_name,
                arguments,
            },
        }
    }
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(deny_unknown_fields)]
pub(in crate::core::checkpoint::v1) struct CheckpointProgramFunction {
    name: String,
    arguments: Value,
}

impl CheckpointProgramFunction {
    pub(in crate::core::checkpoint::v1) fn capture(function: &ProgramFunctionRecord) -> Self {
        Self {
            name: function.name.clone(),
            arguments: function.arguments.clone(),
        }
    }

    pub(in crate::core::checkpoint::v1) fn restore(self) -> ProgramFunctionRecord {
        ProgramFunctionRecord {
            name: self.name,
            arguments: self.arguments,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::CheckpointRuntimeBuiltinToolName;

    #[test]
    fn filesystem_tool_names_keep_the_exact_checkpoint_v1_encoding() {
        // Prepare
        let cases = [
            (
                CheckpointRuntimeBuiltinToolName::FileSystemReadFile,
                "file_system.read_file",
            ),
            (
                CheckpointRuntimeBuiltinToolName::FileSystemWriteFile,
                "file_system.write_file",
            ),
            (
                CheckpointRuntimeBuiltinToolName::FileSystemSearchReplace,
                "file_system.search_replace",
            ),
            (
                CheckpointRuntimeBuiltinToolName::FileSystemBash,
                "file_system.bash",
            ),
        ];

        // Do
        let encoded = cases.map(|(name, _)| serde_json::to_value(name).unwrap());

        // Assert
        assert_eq!(
            encoded,
            cases.map(|(_, expected)| serde_json::Value::String(expected.to_string()))
        );
    }

    #[test]
    fn process_tool_names_keep_the_exact_checkpoint_v1_encoding() {
        // Prepare
        let cases = [
            (
                CheckpointRuntimeBuiltinToolName::ProcessStart,
                "process.start",
            ),
            (
                CheckpointRuntimeBuiltinToolName::ProcessOutput,
                "process.output",
            ),
            (
                CheckpointRuntimeBuiltinToolName::ProcessWrite,
                "process.write",
            ),
            (
                CheckpointRuntimeBuiltinToolName::ProcessList,
                "process.list",
            ),
            (
                CheckpointRuntimeBuiltinToolName::ProcessStop,
                "process.stop",
            ),
        ];

        // Do
        let encoded = cases.map(|(name, _)| serde_json::to_value(name).unwrap());

        // Assert
        assert_eq!(
            encoded,
            cases.map(|(_, expected)| serde_json::Value::String(expected.to_string()))
        );
    }

    #[test]
    fn skill_tool_name_keeps_the_exact_checkpoint_v1_encoding() {
        // Prepare
        let name = CheckpointRuntimeBuiltinToolName::SkillRead;

        // Do
        let encoded = serde_json::to_value(name).unwrap();

        // Assert
        assert_eq!(encoded, serde_json::Value::String("skill.read".to_string()));
    }

    #[test]
    fn subagent_tool_names_keep_the_exact_checkpoint_v1_encoding() {
        // Prepare
        let cases = [
            (
                CheckpointRuntimeBuiltinToolName::SubagentList,
                "subagent.list",
            ),
            (
                CheckpointRuntimeBuiltinToolName::SubagentSpawn,
                "subagent.spawn",
            ),
            (
                CheckpointRuntimeBuiltinToolName::SubagentWait,
                "subagent.wait",
            ),
            (
                CheckpointRuntimeBuiltinToolName::SubagentSendMessage,
                "subagent.send_message",
            ),
            (
                CheckpointRuntimeBuiltinToolName::SubagentInterrupt,
                "subagent.interrupt",
            ),
            (
                CheckpointRuntimeBuiltinToolName::SubagentStop,
                "subagent.stop",
            ),
        ];

        // Do
        let encoded = cases.map(|(name, _)| serde_json::to_value(name).unwrap());

        // Assert
        assert_eq!(
            encoded,
            cases.map(|(_, expected)| serde_json::Value::String(expected.to_string()))
        );
    }
}
