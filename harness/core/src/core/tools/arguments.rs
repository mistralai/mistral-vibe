//! The one argument check of a tool call. Direct calls, program calls and
//! pre-tool hook results all use it; each words a rejection for its reader.

use serde_json::Value;

use crate::core::helpers::schema_error_detail;
use crate::core::tools::external::RuntimeBuiltinToolName;

/// What the model reads before a JSON Schema rejection of a direct call.
pub(crate) const SCHEMA_REJECTION_PREFIX: &str =
    "tool arguments do not satisfy the declared input schema: ";

/// Why a tool's arguments were rejected.
#[derive(Debug)]
pub(crate) enum ArgumentsRejection<'a> {
    /// The tool's JSON Schema rejected the arguments. `explained` is the
    /// tool's own sentence for this error, if it has one.
    Schema {
        error: jsonschema::ValidationError<'a>,
        explained: Option<String>,
    },
    /// A rule the schema cannot state, checked after the schema passed.
    Domain(String),
    /// The JSON Schema itself does not compile.
    InvalidSchema(String),
}

impl ArgumentsRejection<'_> {
    /// The rejection as one line of text, without a caller's prefix.
    pub(crate) fn detail(&self, schema: &Value) -> String {
        match self {
            Self::Schema {
                explained: Some(explained),
                ..
            } => explained.clone(),
            Self::Schema { error, .. } => schema_error_detail(schema, error),
            Self::Domain(message) => message.clone(),
            Self::InvalidSchema(error) => format!("invalid Core-owned JSON Schema: {error}"),
        }
    }
}

/// Checks a tool call's arguments against the tool's JSON Schema, the one
/// source of its structural rules. A built-in tool may word a schema error in
/// its own sentence, and may check rules the schema cannot state once the
/// schema passes. `tool` is `None` for app-provided tools.
pub(crate) fn validate_arguments<'a>(
    tool: Option<RuntimeBuiltinToolName>,
    schema: &Value,
    arguments: &'a Value,
) -> Result<(), ArgumentsRejection<'a>> {
    validate_json_schema(schema, arguments).map_err(|rejection| match rejection {
        ArgumentsRejection::Schema { error, .. } => ArgumentsRejection::Schema {
            explained: tool.and_then(|tool| tool.explain_schema_error(&error)),
            error,
        },
        rejection => rejection,
    })?;
    match tool {
        Some(tool) => tool
            .check_domain(arguments)
            .map_err(ArgumentsRejection::Domain),
        None => Ok(()),
    }
}

/// The JSON Schema check alone, also used for a built-in tool's output.
pub(crate) fn validate_json_schema<'a>(
    schema: &Value,
    value: &'a Value,
) -> Result<(), ArgumentsRejection<'a>> {
    let validator = jsonschema::draft7::options()
        .should_validate_formats(true)
        .build(schema)
        .map_err(|error| ArgumentsRejection::InvalidSchema(error.to_string()))?;
    validator
        .validate(value)
        .map_err(|error| ArgumentsRejection::Schema {
            error,
            explained: None,
        })
}
