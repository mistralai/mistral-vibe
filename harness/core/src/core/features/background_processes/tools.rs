#![expect(
    dead_code,
    reason = "private contract types are reflected by Schemars rather than constructed"
)]

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde_json::Value;

use crate::core::tools::arguments::validate_json_schema;
use crate::core::tools::external::RuntimeBuiltinToolName;
use crate::core::tools::schema::{RequiredNullable, ToolSchema};

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessStartInput {
    #[schemars(length(min = 1))]
    command: String,
    #[schemars(default, skip_serializing_if = "is_default", length(min = 1))]
    cwd: String,
    #[schemars(default, skip_serializing_if = "is_default")]
    env: BTreeMap<String, String>,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessStartOutput {
    process_id: String,
    status: ProcessStatus,
}

#[derive(Default, JsonSchema, serde::Serialize)]
#[schemars(rename_all = "lowercase")]
#[serde(rename_all = "lowercase")]
enum ProcessOutputFrom {
    #[default]
    Start,
    End,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessOutputInput {
    #[schemars(length(min = 1))]
    process_id: String,
    #[schemars(default)]
    from: ProcessOutputFrom,
    #[schemars(default, range(min = 0))]
    cursor: i64,
    #[schemars(default, range(min = 0, max = 30_000))]
    wait_ms: i64,
    #[schemars(default = "default_max_bytes", range(min = 1))]
    max_bytes: i64,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessOutputOutput {
    process_id: String,
    status: ProcessStatus,
    exit_code: RequiredNullable<i64>,
    output: String,
    #[schemars(range(min = 0))]
    output_start_cursor: Option<i64>,
    #[schemars(range(min = 0))]
    next_cursor: i64,
    #[schemars(range(min = 0))]
    bytes_available: i64,
    /// Retained output exists outside this page in the selected read direction.
    has_more: bool,
    /// The requested view crosses an output prefix that retention discarded.
    truncated_before: bool,
}

#[derive(JsonSchema)]
#[schemars(untagged, extend("x-harness-one-of" = true))]
enum ProcessWriteInput {
    Text(ProcessWriteTextInput),
    Control(ProcessWriteControlInput),
    BytesBase64(ProcessWriteBytesInput),
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessWriteTextInput {
    #[schemars(length(min = 1))]
    process_id: String,
    text: String,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessWriteControlInput {
    #[schemars(length(min = 1))]
    process_id: String,
    #[schemars(length(min = 1))]
    control: Vec<ProcessControlKey>,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "snake_case")]
enum ProcessControlKey {
    CtrlC,
    CtrlD,
    CtrlZ,
    Esc,
    Tab,
    Enter,
    Backspace,
    Up,
    Down,
    Left,
    Right,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessWriteBytesInput {
    #[schemars(length(min = 1))]
    process_id: String,
    #[schemars(length(min = 1))]
    bytes_base64: String,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessWriteOutput {
    process_id: String,
    status: ProcessStatus,
    #[schemars(range(min = 0))]
    bytes_written: i64,
}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ProcessListInput {}

#[derive(JsonSchema)]
#[schemars(deny_unknown_fields)]
struct ProcessListOutput {
    processes: Vec<ProcessListItem>,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessListItem {
    process_id: String,
    command: String,
    status: ProcessStatus,
    exit_code: RequiredNullable<i64>,
    output_path: RequiredNullable<String>,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessStopInput {
    #[schemars(length(min = 1))]
    process_id: String,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "camelCase", deny_unknown_fields)]
struct ProcessStopOutput {
    process_id: String,
    status: ProcessStatus,
    exit_code: RequiredNullable<i64>,
}

#[derive(JsonSchema)]
#[schemars(rename_all = "lowercase")]
enum ProcessStatus {
    Running,
    Completed,
    Failed,
    Stopped,
    Orphaned,
}

fn default_max_bytes() -> i64 {
    16_000
}

fn is_default<T: Default + PartialEq>(value: &T) -> bool {
    value == &T::default()
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
pub(crate) enum ToolName {
    Start,
    Output,
    Write,
    List,
    Stop,
}

impl ToolName {
    const ALL: [Self; 5] = [
        Self::Start,
        Self::Output,
        Self::Write,
        Self::List,
        Self::Stop,
    ];

    pub(crate) fn runtime_tool(self) -> RuntimeBuiltinToolName {
        match self {
            Self::Start => RuntimeBuiltinToolName::ProcessStart,
            Self::Output => RuntimeBuiltinToolName::ProcessOutput,
            Self::Write => RuntimeBuiltinToolName::ProcessWrite,
            Self::List => RuntimeBuiltinToolName::ProcessList,
            Self::Stop => RuntimeBuiltinToolName::ProcessStop,
        }
    }

    pub(crate) fn sandbox_name(self) -> &'static str {
        match self {
            Self::Start => "process_start",
            Self::Output => "process_output",
            Self::Write => "process_write",
            Self::List => "process_list",
            Self::Stop => "process_stop",
        }
    }

    pub(crate) fn function_name(self) -> &'static str {
        match self {
            Self::Start => "start",
            Self::Output => "output",
            Self::Write => "write",
            Self::List => "list",
            Self::Stop => "stop",
        }
    }

    fn spec(self) -> ToolSpec {
        match self {
            Self::Start => ToolSpec {
                name: self,
                description: "Start a long-running command in the Runtime execution environment and return immediately with a stable process ID.",
                input_schema: ProcessStartInput::tool_schema(),
                output_schema: ProcessStartOutput::tool_schema(),
            },
            Self::Output => ToolSpec {
                name: self,
                description: "Read terminal output from a background process. Cursors and byte limits refer to raw stored terminal bytes. Use from=start with nextCursor for incremental reads; waitMs then returns when output arrives, the process exits, or the deadline expires. Use from=end for the current newest page.",
                input_schema: ProcessOutputInput::tool_schema(),
                output_schema: ProcessOutputOutput::tool_schema(),
            },
            Self::Write => ToolSpec {
                name: self,
                description: "Send exactly one text, control-key, or base64 byte payload to a running background process.",
                input_schema: ProcessWriteInput::tool_schema(),
                output_schema: ProcessWriteOutput::tool_schema(),
            },
            Self::List => ToolSpec {
                name: self,
                description: "List background processes owned by this agent session and their current status.",
                input_schema: ProcessListInput::tool_schema(),
                output_schema: ProcessListOutput::tool_schema(),
            },
            Self::Stop => ToolSpec {
                name: self,
                description: "Stop a background process and its child process group.",
                input_schema: ProcessStopInput::tool_schema(),
                output_schema: ProcessStopOutput::tool_schema(),
            },
        }
    }
}

pub(crate) struct ToolSpec {
    pub(crate) name: ToolName,
    pub(crate) description: &'static str,
    pub(crate) input_schema: Value,
    pub(crate) output_schema: Value,
}

pub(crate) fn tools() -> Vec<ToolSpec> {
    ToolName::ALL.into_iter().map(ToolName::spec).collect()
}

const WRITE_PAYLOADS: [&str; 3] = ["text", "control", "bytesBase64"];

/// A `process.write` call is one of 3 alternatives, and the schema reports a
/// bad call only as "not valid under any of the schemas listed in the 'oneOf'
/// keyword". This says what to fix: the payload count, or the error of the
/// one alternative the call picked.
pub(crate) fn explain_write_schema_error(
    error: &jsonschema::ValidationError<'_>,
) -> Option<String> {
    if !error.instance_path().as_str().is_empty() || error.kind().keyword() != "oneOf" {
        return None;
    }
    let arguments = error.instance().as_object()?;
    let mut payloads = WRITE_PAYLOADS.into_iter().filter(|payload| {
        arguments
            .get(*payload)
            .is_some_and(|value| !value.is_null())
    });
    let (Some(payload), None) = (payloads.next(), payloads.next()) else {
        return Some("write requires exactly one of text, control, or bytesBase64".to_string());
    };
    let schema = ProcessWriteInput::tool_schema();
    let alternative = schema["oneOf"]
        .as_array()?
        .iter()
        .find(|alternative| alternative["properties"].get(payload).is_some())?;
    validate_json_schema(alternative, error.instance())
        .err()
        .map(|rejection| rejection.detail(alternative))
}

pub(crate) fn is_sandbox_name(name: &str) -> bool {
    ToolName::ALL
        .into_iter()
        .any(|tool_name| tool_name.sandbox_name() == name)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    #[test]
    fn a_bad_write_reads_in_words() {
        let write = tools()
            .into_iter()
            .find(|tool| tool.name == ToolName::Write)
            .unwrap();
        let validator = jsonschema::draft7::new(&write.input_schema).unwrap();
        let explain = |arguments: Value| {
            validator
                .validate(&arguments)
                .err()
                .map(|error| explain_write_schema_error(&error).unwrap())
        };

        assert_eq!(explain(json!({"processId": "p", "text": "ls\n"})), None);
        for arguments in [
            json!({"processId": "p"}),
            json!({"processId": "p", "text": null}),
            json!({"processId": "p", "text": "y", "control": ["enter"]}),
        ] {
            assert_eq!(
                explain(arguments).as_deref(),
                Some("write requires exactly one of text, control, or bytesBase64")
            );
        }
        assert_eq!(
            explain(json!({"text": "y"})).as_deref(),
            Some("\"processId\" is a required property")
        );
        assert_eq!(
            explain(json!({"processId": "", "text": "y"})).as_deref(),
            Some("at /processId: \"\" is shorter than 1 character")
        );
        assert_eq!(
            explain(json!({"processId": "p", "control": ["F1"]})).as_deref(),
            Some(
                "at /control/0: \"F1\" is not one of \"ctrl_c\", \"ctrl_d\" or 9 other candidates"
            )
        );
        assert!(
            explain(json!({"processId": "p", "text": null, "control": ["enter"]}))
                .unwrap()
                .starts_with("Additional properties are not allowed ('text' was unexpected)")
        );
        assert!(
            explain(json!({"processId": "p", "text": "y", "proc": 1}))
                .unwrap()
                .starts_with("Additional properties are not allowed ('proc' was unexpected)")
        );
    }

    #[test]
    fn output_schema_preserves_bounded_inspection_controls() {
        // Prepare
        let tools = tools();

        // Do
        let output = tools
            .iter()
            .find(|tool| tool.name == ToolName::Output)
            .unwrap();

        // Assert
        assert_eq!(
            output.input_schema["properties"]["from"]["enum"],
            json!(["start", "end"])
        );
        assert_eq!(
            output.output_schema["required"],
            json!([
                "processId",
                "status",
                "exitCode",
                "output",
                "nextCursor",
                "bytesAvailable",
                "hasMore",
                "truncatedBefore"
            ])
        );
        assert_eq!(
            output.output_schema["properties"]["outputStartCursor"]["minimum"],
            0
        );
    }

    #[test]
    fn public_output_and_list_inputs_expose_bounded_schema() {
        let tools = tools();
        let output = tools
            .iter()
            .find(|tool| tool.name == ToolName::Output)
            .unwrap();
        let max_bytes = &output.input_schema["properties"]["maxBytes"];
        let list = tools
            .iter()
            .find(|tool| tool.name == ToolName::List)
            .unwrap();

        assert_eq!(max_bytes["default"], json!(16_000));
        assert_eq!(max_bytes["minimum"], json!(1));
        assert!(max_bytes.get("maximum").is_none());
        assert_eq!(
            output.input_schema["properties"]["waitMs"]["maximum"],
            json!(30_000)
        );
        assert_eq!(
            list.input_schema,
            json!({
                "type": "object",
                "properties": {},
                "additionalProperties": false,
            })
        );
    }
}
