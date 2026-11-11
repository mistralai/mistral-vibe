#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

mod bash_timeout;
mod settings;

use schemars::JsonSchema;
use serde_json::{Value, json};

use crate::core::tools::command_environment::CommandEnvironment;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::ToolSchema;

#[cfg(any(test, feature = "python-binding"))]
pub(crate) use bash_timeout::restored_bash_call;
pub(crate) use bash_timeout::{BashTimeout, BashTimeoutSettings, bash_runtime_arguments};

pub(crate) use settings::{Mode, ReadFileDocuments, ReadFileImages, ReadFileSupport};

pub(crate) const NAMESPACE: &str = "file_system";
const READ_FILE_DESCRIPTION: &str = "Read a text file with optional zero-indexed line offset and limit. When `was_truncated` is true, read on from `next_offset`. An offset past the end of the file is an error. A directory reads as a listing of its entries, one per line with `/` after each subdirectory, at most 500 at a time.";
const READ_FILE_EXTRACTED_DOCUMENTS: &str =
    "PDF, Word, PowerPoint, and email files return their extracted text.";
const READ_FILE_IMAGE_TEXT: &str = "An image file returns only the text found in the image.";
const READ_FILE_NATIVE_IMAGE: &str = "An image file returns the image itself.";
const WRITE_FILE_DESCRIPTION: &str = "Create or overwrite a UTF-8 file. Line breaks in `content` can be `\\n`, `\\r\\n` or `\\r`: the file keeps the line endings of the file it replaces, and a new file uses the repository's line endings when they can be found, or the platform default otherwise. To change a file's line endings, run a command such as `unix2dos` or `dos2unix`.";
const EDIT_DESCRIPTION: &str = r#"Exact string replacement in a file.

Read the current file first. `old_string` must match exactly, including whitespace and indentation. Line endings don't need to match: the file keeps its own. With `replace_all=false` (the default), `old_string` must occur exactly once; set `replace_all=true` only to replace every occurrence. If an edit fails, re-read the file before retrying.

Example:
{"file_path":"src/config.ts","old_string":"const mode = \"old\";","new_string":"const mode = \"new\";","replace_all":false}"#;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) enum Exposure {
    Direct,
    Programmatic,
}

fn read_file_description(support: ReadFileSupport, exposure: Exposure) -> String {
    let documents = match support.documents {
        ReadFileDocuments::Unsupported => None,
        ReadFileDocuments::ExtractedText => Some(READ_FILE_EXTRACTED_DOCUMENTS),
    };
    let images = match (support.images, exposure) {
        (ReadFileImages::Unsupported, _) => None,
        (ReadFileImages::ExtractedText, _) => Some(READ_FILE_IMAGE_TEXT),
        (ReadFileImages::Native, Exposure::Direct) => Some(READ_FILE_NATIVE_IMAGE),
        // A program cannot look at an image, and the model may never see it.
        (ReadFileImages::Native, Exposure::Programmatic) => None,
    };
    [Some(READ_FILE_DESCRIPTION), documents, images]
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join(" ")
}

pub(crate) fn search_description(environment: CommandEnvironment) -> String {
    match environment.profile() {
        Some(profile) => format!(
            "Tools to interact with the file system: read, write, edit, and {}.",
            profile.tool_name
        ),
        None => "Tools to interact with the file system: read, write, and edit.".to_string(),
    }
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
    /// Size of the file in bytes; for a directory, of its whole listing.
    file_size_bytes: i64,
    returned_bytes: i64,
    offset: i64,
    lines_read: i64,
    /// Whether the file has more content past what was returned.
    was_truncated: bool,
    /// Number of lines in the file, or of entries in a directory, or null when the file is too large to count.
    total_lines: Option<i64>,
    /// The `offset` to read the rest of the file from, or null when the read reached the end of the file.
    next_offset: Option<i64>,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct WriteFileInput {
    /// Path of the file to create or overwrite. `file_path` is accepted as an alias, as the `edit` tool names it.
    path: String,
    content: String,
}

/// Lets a `write_file` call name its target `file_path`, the way the skill docs
/// and the `edit` tool do, while the Runtime keeps reading `path`: the schema
/// takes either name, and [`write_file_runtime_arguments`] renames the alias.
fn write_file_input_schema(schema: &mut Value) {
    let Some(properties) = schema.get_mut("properties").and_then(Value::as_object_mut) else {
        return;
    };
    let Some(path) = properties.get("path").cloned() else {
        return;
    };
    let mut file_path = path;
    if let Value::Object(fields) = &mut file_path {
        fields.insert(
            "description".to_string(),
            Value::from("Alias for `path`, the way the `edit` tool names the file."),
        );
    }
    properties.insert("file_path".to_string(), file_path);
    if let Some(required) = schema.get_mut("required").and_then(Value::as_array_mut) {
        required.retain(|field| field.as_str() != Some("path"));
    }
    schema["anyOf"] = json!([{ "required": ["path"] }, { "required": ["file_path"] }]);
}

/// Renames a `write_file` call's `file_path` to the `path` the Runtime reads.
/// A call that says both keeps its `path` and drops the alias.
pub(crate) fn write_file_runtime_arguments(mut arguments: Value) -> Value {
    if let Value::Object(fields) = &mut arguments {
        if fields.contains_key("path") {
            fields.remove("file_path");
        } else if let Some(file_path) = fields.remove("file_path") {
            fields.insert("path".to_string(), file_path);
        }
    }
    arguments
}

/// A `write_file` call that names the file twice must mean the same file by both names.
pub(crate) fn check_write_file_domain(arguments: &Value) -> Result<(), String> {
    let (Some(path), Some(file_path)) = (arguments.get("path"), arguments.get("file_path")) else {
        return Ok(());
    };
    if path == file_path {
        Ok(())
    } else {
        Err(
            "write_file got both `path` and `file_path` with different values; send only one"
                .to_string(),
        )
    }
}

#[derive(JsonSchema)]
struct WriteFileOutput {
    path: String,
    bytes_written: i64,
    file_existed: bool,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct EditInput {
    /// Path to the existing text file to edit. Use `file_path`; `path` is not accepted.
    file_path: String,
    #[schemars(length(min = 1))]
    /// Exact text to find, including whitespace and indentation. It must occur exactly once unless `replace_all` is true.
    old_string: String,
    /// Exact replacement text for `old_string`. It may be empty to delete the matched text, but it must differ from `old_string`.
    new_string: String,
    #[schemars(default)]
    /// When false (the default), replace the single `old_string` match. When true, replace every occurrence of `old_string`.
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
    /// Seconds the command may run; `BashTimeout` fills in the Host's default and maximum.
    #[schemars(default, range(min = 1))]
    timeout_seconds: i64,
}

#[derive(JsonSchema)]
struct BashOutput {
    command: String,
    stdout: String,
    stderr: String,
    returncode: i64,
    was_truncated: bool,
    /// Whether the command was stopped at its timeout; `stdout` and `stderr` hold the output written before it.
    #[schemars(default)]
    timed_out: bool,
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
            Self::Edit => RuntimeBuiltinToolName::FileSystemEdit,
            Self::Bash => RuntimeBuiltinToolName::FileSystemBash,
        }
    }

    fn schemas(self, timeout: BashTimeout) -> (Value, Value) {
        match self {
            Self::ReadFile => (ReadFileInput::tool_schema(), ReadFileOutput::tool_schema()),
            Self::WriteFile => {
                let mut input_schema = WriteFileInput::tool_schema();
                write_file_input_schema(&mut input_schema);
                (input_schema, WriteFileOutput::tool_schema())
            }
            Self::Edit => (EditInput::tool_schema(), EditOutput::tool_schema()),
            Self::Bash => {
                let mut input_schema = BashInput::tool_schema();
                timeout.apply_to_input_schema(&mut input_schema);
                (input_schema, BashOutput::tool_schema())
            }
        }
    }

    fn spec(
        self,
        environment: CommandEnvironment,
        timeout: BashTimeout,
        background: bool,
    ) -> Option<ToolSpec> {
        let direct_name = self.direct_name(environment)?;
        let description = match self {
            Self::ReadFile => READ_FILE_DESCRIPTION,
            Self::WriteFile => WRITE_FILE_DESCRIPTION,
            Self::Edit => EDIT_DESCRIPTION,
            Self::Bash => environment.profile()?.bash_description(background),
        };
        let (mut input_schema, output_schema) = self.schemas(timeout);
        if self == Self::Bash
            && let Some(hint) = environment.profile()?.timeout_hint(background)
        {
            let description = &mut input_schema["properties"]["timeout_seconds"]["description"];
            let base = description.as_str().unwrap_or_default();
            *description = Value::from(format!("{base} {hint}"));
        }
        Some(ToolSpec {
            name: self,
            direct_name,
            description: description.to_string(),
            input_schema,
            output_schema,
        })
    }
}

pub(crate) struct ToolSpec {
    pub(crate) name: ToolName,
    pub(crate) direct_name: &'static str,
    pub(crate) description: String,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) struct RuntimeToolContract {
    pub(crate) name: RuntimeBuiltinToolName,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

/// The file system tools. `background` says whether background processes are
/// enabled; only then does the bash tool advise starting long work in the
/// background.
pub(crate) fn tools(
    environment: CommandEnvironment,
    timeout: BashTimeout,
    background: bool,
) -> Vec<ToolSpec> {
    ToolName::ALL
        .into_iter()
        .filter_map(|name| name.spec(environment, timeout, background))
        .collect()
}

/// The file system tools as `exposure` shows them: `read_file` also says what
/// the Runtime declared it returns.
pub(crate) fn exposed_tools(
    environment: CommandEnvironment,
    timeout: BashTimeout,
    background: bool,
    read_file: ReadFileSupport,
    exposure: Exposure,
) -> Vec<ToolSpec> {
    tools(environment, timeout, background)
        .into_iter()
        .map(|spec| match spec.name {
            ToolName::ReadFile => ToolSpec {
                description: read_file_description(read_file, exposure),
                ..spec
            },
            _ => spec,
        })
        .collect()
}

pub(crate) fn runtime_contracts(timeout: BashTimeout) -> Vec<RuntimeToolContract> {
    ToolName::ALL
        .into_iter()
        .map(|name| {
            let (input_schema, output_schema) = name.schemas(timeout);
            RuntimeToolContract {
                name: name.runtime_tool(),
                input_schema,
                output_schema,
            }
        })
        .collect()
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

    /// *Prepare*: The Core-owned edit tool.
    /// *Do*: Compare the schema the model sees with the Runtime contract.
    /// *Assert*: They are the same schema, so the Runtime receives the model's arguments as they are.
    #[test]
    fn runtime_edit_contract_is_the_model_edit_schema() {
        // Prepare
        let edit = ToolName::Edit
            .spec(CommandEnvironment::Unix, BashTimeout::default(), false)
            .expect("Unix exposes edit");

        // Do
        let contracts = runtime_contracts(BashTimeout::default());
        let contract = contracts
            .iter()
            .find(|contract| contract.name == RuntimeBuiltinToolName::FileSystemEdit)
            .unwrap();

        // Assert
        assert_eq!(contract.input_schema, edit.input_schema);
        assert_eq!(
            contract.input_schema["required"],
            json!(["file_path", "old_string", "new_string"])
        );
    }

    /// *Prepare*: The Core-owned write_file tool, as the model and the Runtime see it.
    /// *Do*: Validate calls that name the file with `path`, with its `file_path` alias, and with neither.
    /// *Assert*: Both names pass; a call without either, or with a non-string alias, is rejected.
    #[test]
    fn write_file_schema_accepts_file_path_as_an_alias_for_path() {
        // Prepare
        let write_file = ToolName::WriteFile
            .spec(CommandEnvironment::Unix, BashTimeout::default(), false)
            .expect("Unix exposes write_file");
        let contracts = runtime_contracts(BashTimeout::default());
        let contract = contracts
            .iter()
            .find(|contract| contract.name == RuntimeBuiltinToolName::FileSystemWriteFile)
            .unwrap();

        // Assert: the Runtime contract is the model's schema, so both accept the alias.
        assert_eq!(contract.input_schema, write_file.input_schema);
        for arguments in [
            json!({"path": "a.txt", "content": "x"}),
            json!({"file_path": "a.txt", "content": "x"}),
        ] {
            assert!(
                crate::core::tools::arguments::validate_arguments(
                    Some(RuntimeBuiltinToolName::FileSystemWriteFile),
                    &contract.input_schema,
                    &arguments,
                )
                .is_ok(),
                "{arguments} should validate"
            );
        }
        for arguments in [
            json!({"content": "x"}),
            json!({"file_path": 7, "content": "x"}),
        ] {
            assert!(
                crate::core::tools::arguments::validate_arguments(
                    Some(RuntimeBuiltinToolName::FileSystemWriteFile),
                    &contract.input_schema,
                    &arguments,
                )
                .is_err(),
                "{arguments} should not validate"
            );
        }
    }

    /// *Prepare*: `write_file` calls naming the file once, and once by both names.
    /// *Do*: Rename the arguments the Runtime takes.
    /// *Assert*: `file_path` becomes `path`; a `path` already present wins and the alias is dropped.
    #[test]
    fn write_file_runtime_arguments_rename_the_file_path_alias() {
        assert_eq!(
            write_file_runtime_arguments(json!({"file_path": "a.txt", "content": "x"})),
            json!({"path": "a.txt", "content": "x"})
        );
        assert_eq!(
            write_file_runtime_arguments(json!({"path": "a.txt", "content": "x"})),
            json!({"path": "a.txt", "content": "x"})
        );
        assert_eq!(
            write_file_runtime_arguments(
                json!({"path": "a.txt", "file_path": "a.txt", "content": "x"})
            ),
            json!({"path": "a.txt", "content": "x"})
        );
    }

    /// *Prepare*: `write_file` calls naming the file by both names, equal and different.
    /// *Do*: Check the rule the schema cannot state.
    /// *Assert*: Equal names pass; different names are refused.
    #[test]
    fn write_file_domain_rejects_two_different_file_names() {
        assert!(check_write_file_domain(&json!({"file_path": "a.txt"})).is_ok());
        assert!(check_write_file_domain(&json!({"path": "a.txt", "file_path": "a.txt"})).is_ok());
        assert!(check_write_file_domain(&json!({"path": "a.txt", "file_path": "b.txt"})).is_err());
    }
}
