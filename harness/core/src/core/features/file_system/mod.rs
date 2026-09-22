#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

pub(crate) const NAMESPACE: &str = "file_system";
const MAX_BASH_TIMEOUT_SECONDS: i64 = 300;
const EDIT_DESCRIPTION: &str = r#"Exact string replacement in a file.

Read the current file first. `old_string` must match exactly, including whitespace, indentation, and line endings. With `replace_all=false` (the default), `old_string` must occur exactly once; set `replace_all=true` only to replace every occurrence. If an edit fails, re-read the file before retrying.

Example:
{"file_path":"src/config.ts","old_string":"const mode = \"old\";","new_string":"const mode = \"new\";","replace_all":false}"#;

pub(crate) fn search_description(environment: CommandEnvironment) -> String {
    match environment.profile() {
        Some(profile) => format!(
            "Tools to interact with the file system: read, write, edit, and {}.",
            profile.tool_name
        ),
        None => "Tools to interact with the file system: read, write, and edit.".to_string(),
    }
}

fn default_bash_timeout_seconds() -> i64 {
    MAX_BASH_TIMEOUT_SECONDS
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ReadFileInput {
    path: String,
    #[schemars(default, range(min = 0))]
    offset: i64,
    #[schemars(default, range(min = 1))]
    limit: Option<i64>,
}

#[derive(JsonSchema)]
struct ReadFileOutput {
    path: String,
    content: String,
    file_size_bytes: i64,
    returned_bytes: i64,
    offset: i64,
    lines_read: i64,
    was_truncated: bool,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct WriteFileInput {
    path: String,
    content: String,
}

#[derive(JsonSchema)]
struct WriteFileOutput {
    path: String,
    bytes_written: i64,
    file_existed: bool,
}

#[derive(Deserialize, JsonSchema)]
#[schemars(deny_unknown_fields)]
#[serde(deny_unknown_fields)]
struct EditInput {
    /// Path to the existing text file to edit. Use `file_path`; `path` is not accepted.
    file_path: String,
    #[schemars(length(min = 1))]
    /// Exact text to find, including whitespace, indentation, and line endings. It must occur exactly once unless `replace_all` is true.
    old_string: String,
    /// Exact replacement text for `old_string`. It may be empty to delete the matched text, but it must differ from `old_string`.
    new_string: String,
    #[serde(default)]
    #[schemars(default)]
    /// When false (the default), replace the single `old_string` match. When true, replace every occurrence of `old_string`.
    replace_all: bool,
}

#[derive(JsonSchema, Serialize)]
#[schemars(deny_unknown_fields)]
struct RuntimeSearchReplaceInput {
    file_path: String,
    #[schemars(length(min = 1))]
    content: Vec<RuntimeSearchReplaceBlock>,
}

#[derive(JsonSchema, Serialize)]
#[schemars(deny_unknown_fields)]
struct RuntimeSearchReplaceBlock {
    #[schemars(length(min = 1))]
    old_str: String,
    new_str: String,
    #[schemars(default)]
    replace_all: bool,
}

#[derive(JsonSchema)]
struct EditOutput {
    file: String,
    lines_changed: i64,
    warnings: Vec<String>,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct BashInput {
    command: String,
    #[schemars(
        default = "default_bash_timeout_seconds",
        range(min = 1, max = MAX_BASH_TIMEOUT_SECONDS)
    )]
    timeout_seconds: i64,
}

#[derive(JsonSchema)]
struct BashOutput {
    command: String,
    stdout: String,
    stderr: String,
    returncode: i64,
    was_truncated: bool,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ToolName {
    ReadFile,
    WriteFile,
    Edit,
    Bash,
}

impl ToolName {
    const ALL: [Self; 4] = [Self::ReadFile, Self::WriteFile, Self::Edit, Self::Bash];

    pub(crate) fn direct_name(self, environment: CommandEnvironment) -> Option<&'static str> {
        match self {
            Self::ReadFile => Some("read_file"),
            Self::WriteFile => Some("write_file"),
            Self::Edit => Some("edit"),
            Self::Bash => environment.profile().map(|profile| profile.tool_name),
        }
    }

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::ReadFile => RuntimeBuiltinToolName::FileSystemReadFile,
            Self::WriteFile => RuntimeBuiltinToolName::FileSystemWriteFile,
            Self::Edit => RuntimeBuiltinToolName::FileSystemSearchReplace,
            Self::Bash => RuntimeBuiltinToolName::FileSystemBash,
        }
    }

    fn schemas(self) -> (Value, Value) {
        match self {
            Self::ReadFile => (ReadFileInput::tool_schema(), ReadFileOutput::tool_schema()),
            Self::WriteFile => (
                WriteFileInput::tool_schema(),
                WriteFileOutput::tool_schema(),
            ),
            Self::Edit => (EditInput::tool_schema(), EditOutput::tool_schema()),
            Self::Bash => (BashInput::tool_schema(), BashOutput::tool_schema()),
        }
    }

    fn runtime_input_schema(self) -> Value {
        match self {
            Self::Edit => RuntimeSearchReplaceInput::tool_schema(),
            _ => self.schemas().0,
        }
    }

    fn spec(self, environment: CommandEnvironment) -> Option<ToolSpec> {
        let direct_name = self.direct_name(environment)?;
        let description = match self {
            Self::ReadFile => "Read a text file with optional zero-indexed line offset and limit.",
            Self::WriteFile => "Create or overwrite a UTF-8 file.",
            Self::Edit => EDIT_DESCRIPTION,
            Self::Bash => environment.profile()?.tool_description,
        };
        let (input_schema, output_schema) = self.schemas();
        Some(ToolSpec {
            name: self,
            direct_name,
            description,
            input_schema,
            output_schema,
        })
    }
}

pub(crate) struct ToolSpec {
    pub(crate) name: ToolName,
    pub(crate) direct_name: &'static str,
    pub(crate) description: &'static str,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) struct RuntimeToolContract {
    pub(crate) name: RuntimeBuiltinToolName,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) fn tools(environment: CommandEnvironment) -> Vec<ToolSpec> {
    ToolName::ALL
        .into_iter()
        .filter_map(|name| name.spec(environment))
        .collect()
}

pub(crate) fn runtime_contracts() -> Vec<RuntimeToolContract> {
    ToolName::ALL
        .into_iter()
        .map(|name| {
            let input_schema = name.runtime_input_schema();
            let (_, output_schema) = name.schemas();
            RuntimeToolContract {
                name: name.runtime_tool(),
                input_schema,
                output_schema,
            }
        })
        .collect()
}

/// Translates arguments that already passed the model-visible `EditInput` schema.
pub(crate) fn edit_runtime_arguments(arguments: Value) -> Value {
    let arguments: EditInput = serde_json::from_value(arguments)
        .expect("edit arguments satisfy the model-visible schema before translation");
    serde_json::to_value(RuntimeSearchReplaceInput {
        file_path: arguments.file_path,
        content: vec![RuntimeSearchReplaceBlock {
            old_str: arguments.old_string,
            new_str: arguments.new_string,
            replace_all: arguments.replace_all,
        }],
    })
    .expect("Runtime edit arguments serialize")
}

pub(crate) fn direct_tool_name(environment: CommandEnvironment, name: &str) -> Option<ToolName> {
    ToolName::ALL
        .into_iter()
        .find(|tool_name| tool_name.direct_name(environment) == Some(name))
}

pub(crate) fn is_direct_name(environment: CommandEnvironment, name: &str) -> bool {
    direct_tool_name(environment, name).is_some()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    ///
    /// *Prepare*: The Core-owned schema for the built-in bash tool.
    /// *Do*: Read the model-visible timeout property.
    /// *Assert*: Omitted timeout defaults to 300 seconds and explicit values cannot exceed 300.
    ///
    #[test]
    fn bash_timeout_schema_has_the_portable_default_and_maximum() {
        // Prepare
        let bash = ToolName::Bash
            .spec(CommandEnvironment::Unix)
            .expect("Unix exposes Bash");

        // Do
        let timeout = &bash.input_schema["properties"]["timeout_seconds"];

        // Assert
        assert_eq!(timeout["default"], 300);
        assert_eq!(timeout["maximum"], 300);
    }

    #[test]
    fn model_edit_arguments_translate_to_the_existing_runtime_contract() {
        assert_eq!(
            edit_runtime_arguments(json!({
                "file_path": "src/lib.rs",
                "old_string": "before",
                "new_string": "after",
            })),
            json!({
                "file_path": "src/lib.rs",
                "content": [{
                    "old_str": "before",
                    "new_str": "after",
                    "replace_all": false,
                }],
            }),
        );
    }

    #[test]
    fn runtime_edit_schema_keeps_the_existing_wire_shape() {
        let contracts = runtime_contracts();
        let edit = contracts
            .iter()
            .find(|contract| contract.name == RuntimeBuiltinToolName::FileSystemSearchReplace)
            .unwrap();

        assert_eq!(
            edit.input_schema["required"],
            json!(["file_path", "content"])
        );
        assert!(edit.input_schema["properties"].get("old_string").is_none());
        assert!(
            edit.input_schema["properties"]["content"]["items"]["properties"]
                .get("old_str")
                .is_some()
        );
    }
}
