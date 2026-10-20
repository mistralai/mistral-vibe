use jsonschema::error::ValidationErrorKind;
use serde_json::Value;

use crate::core::config::HarnessConfig;
use crate::core::error::CoreError;
use crate::core::features::file_system;
use crate::core::tools::external::{
    ExternalTool, ExternalToolCall, RuntimeBuiltinToolName, ToolTarget,
};
use crate::core::tools::resolved::assembly;

#[derive(Clone, Debug, PartialEq)]
pub(super) struct ToolContracts {
    runtime: Vec<RuntimeContract>,
    provided: Vec<ProvidedInputContract>,
}

#[derive(Clone, Debug, PartialEq)]
struct RuntimeContract {
    name: RuntimeBuiltinToolName,
    input_schema: Value,
    output_schema: Option<Value>,
}

#[derive(Clone, Debug, PartialEq)]
struct ProvidedInputContract {
    group_name: String,
    tool_name: String,
    input_schema: Value,
}

impl ToolContracts {
    pub(super) fn compile(config: &HarnessConfig) -> Self {
        let mut runtime = file_system::runtime_contracts()
            .into_iter()
            .map(|contract| RuntimeContract {
                name: contract.name,
                input_schema: contract.input_schema,
                output_schema: Some(contract.output_schema),
            })
            .collect::<Vec<_>>();
        runtime.push(RuntimeContract {
            name: RuntimeBuiltinToolName::SelfWait,
            input_schema: crate::core::features::model_wait::runtime_input_schema(
                config.settings.tools.max_wait_ms,
            ),
            output_schema: Some(crate::core::features::model_wait::output_schema()),
        });
        runtime.extend(
            assembly::non_filesystem_runtime_builtin_tools(
                config.settings.tools.programmatic.max_sleep_ms,
            )
            .into_iter()
            .map(|tool| {
                let ToolTarget::RuntimeBuiltin { name } = tool.target else {
                    unreachable!("resolved Runtime built-ins contain a provided tool")
                };
                RuntimeContract {
                    name,
                    input_schema: tool.descriptor.input_schema,
                    output_schema: tool.descriptor.output_schema,
                }
            }),
        );
        // These contracts only settle persisted Actions. They install no executable routes,
        // and current catalog schemas must not reinterpret a previously accepted result.
        runtime.extend(
            RuntimeBuiltinToolName::DEPRECATED.map(|name| RuntimeContract {
                name,
                input_schema: Value::Bool(true),
                output_schema: Some(Value::Bool(true)),
            }),
        );
        let provided = config
            .tool_groups()
            .flat_map(|group| {
                group.tools.iter().map(|tool| ProvidedInputContract {
                    group_name: group.name.clone(),
                    tool_name: tool.name.clone(),
                    input_schema: tool.input_schema.clone(),
                })
            })
            .collect();
        Self { runtime, provided }
    }

    pub(super) fn resolve_effective_call(
        &self,
        original: &ExternalToolCall,
        arguments: Value,
    ) -> Result<ExternalToolCall, CoreError> {
        validate_json_schema(self.input_schema(&original.call)?, &arguments).map_err(|error| {
            CoreError::invalid_command(format!(
                "pre-tool hook arguments do not satisfy the original tool schema: {error}"
            ))
        })?;
        let arguments = match &original.call {
            ExternalTool::RuntimeBuiltin {
                name: RuntimeBuiltinToolName::SelfWait,
                arguments: original_arguments,
                ..
            } => crate::core::features::model_wait::clamp_hook_arguments(
                original_arguments,
                arguments,
            )?,
            _ => arguments,
        };
        Ok(ExternalToolCall {
            action_id: original.action_id.clone(),
            call_id: original.call_id.clone(),
            origin: original.origin,
            call: original.call.with_arguments(arguments),
        })
    }

    pub(super) fn validate_input(
        &self,
        target: &ToolTarget,
        value: &Value,
    ) -> Result<(), CoreError> {
        validate_model_input(self.input_schema_for_target(target)?, value)
    }

    pub(super) fn validate_runtime_builtin_output(
        &self,
        name: RuntimeBuiltinToolName,
        value: &Value,
    ) -> Result<(), String> {
        let schema = self
            .runtime_contract(name)
            .output_schema
            .as_ref()
            .expect("Runtime built-ins must declare an output schema");
        validate_json_schema(schema, value)
    }

    fn input_schema(&self, tool: &ExternalTool) -> Result<&Value, CoreError> {
        match tool {
            ExternalTool::RuntimeBuiltin { name, .. } => {
                Ok(&self.runtime_contract(*name).input_schema)
            }
            ExternalTool::Provided {
                group_name,
                tool_name,
                ..
            } => self.provided_input_schema(group_name, tool_name),
        }
    }

    fn input_schema_for_target(&self, target: &ToolTarget) -> Result<&Value, CoreError> {
        match target {
            ToolTarget::RuntimeBuiltin { name } => Ok(&self.runtime_contract(*name).input_schema),
            ToolTarget::Provided {
                group_name,
                tool_name,
            } => self.provided_input_schema(group_name, tool_name),
        }
    }

    fn provided_input_schema(
        &self,
        group_name: &str,
        tool_name: &str,
    ) -> Result<&Value, CoreError> {
        self.provided
            .iter()
            .find(|contract| contract.group_name == group_name && contract.tool_name == tool_name)
            .map(|contract| &contract.input_schema)
            .ok_or_else(|| {
                CoreError::invariant(format!(
                    "provided tool {group_name}.{tool_name} disappeared from resolved tools"
                ))
            })
    }

    fn runtime_contract(&self, name: RuntimeBuiltinToolName) -> &RuntimeContract {
        self.runtime
            .iter()
            .find(|contract| contract.name == name)
            .expect("resolved Runtime built-ins are complete")
    }
}

pub(super) fn validate_model_input(schema: &Value, value: &Value) -> Result<(), CoreError> {
    validate_json_schema(schema, value).map_err(|error| {
        CoreError::invalid_command(format!(
            "tool arguments do not satisfy the declared input schema: {error}"
        ))
    })
}

fn validate_json_schema(schema: &Value, value: &Value) -> Result<(), String> {
    let validator = jsonschema::draft7::options()
        .should_validate_formats(true)
        .build(schema)
        .map_err(|error| format!("invalid Core-owned JSON Schema: {error}"))?;
    validator
        .validate(value)
        .map_err(|error| schema_error_detail(schema, &error))
}

/// Most allowed field names listed back to the model for an unknown field.
const LISTED_FIELDS: usize = 24;

/// The validator's message, made actionable for the model: where in the
/// arguments the problem is and, for an unknown field, which fields the tool
/// accepts and which one the model most likely meant.
fn schema_error_detail(schema: &Value, error: &jsonschema::ValidationError<'_>) -> String {
    let mut detail = error.to_string();
    let location = error.instance_path().as_str();
    if !location.is_empty() {
        detail = format!("at {location}: {detail}");
    }
    let ValidationErrorKind::AdditionalProperties { unexpected } = error.kind() else {
        return detail;
    };
    let allowed = allowed_fields(schema, error.schema_path().as_str());
    if allowed.is_empty() {
        return detail;
    }
    detail.push_str(". Allowed fields: ");
    detail.push_str(
        &allowed
            .iter()
            .take(LISTED_FIELDS)
            .cloned()
            .collect::<Vec<_>>()
            .join(", "),
    );
    if allowed.len() > LISTED_FIELDS {
        detail.push_str(", ...");
    }
    detail.push('.');
    for name in unexpected {
        if let Some(field) = closest_field(name, &allowed) {
            detail.push_str(&format!(" Use {field:?} instead of {name:?}."));
        }
    }
    detail
}

/// The properties declared by the schema object whose `additionalProperties`
/// rejected a field.
fn allowed_fields(schema: &Value, keyword_location: &str) -> Vec<String> {
    let Some(object_location) = keyword_location.strip_suffix("/additionalProperties") else {
        return Vec::new();
    };
    schema
        .pointer(object_location)
        .and_then(|object| object.get("properties"))
        .and_then(Value::as_object)
        .map(|properties| properties.keys().cloned().collect())
        .unwrap_or_default()
}

/// The declared field an unknown field most likely stands for: the same name
/// in another case or separator style, a longer or shorter form of it
/// (`timeout` for `timeout_seconds`, `file_path` for `path`), or a near
/// spelling. `None` when no field is a clear match.
fn closest_field<'a>(unexpected: &str, allowed: &'a [String]) -> Option<&'a str> {
    let normalized = normalize_field(unexpected);
    if normalized.is_empty() {
        return None;
    }
    let same_name = allowed
        .iter()
        .find(|field| normalize_field(field) == normalized);
    let longer_form = || {
        allowed
            .iter()
            .filter(|field| {
                let field = normalize_field(field);
                normalized.len() >= 3
                    && (field.starts_with(&normalized) || field.ends_with(&normalized))
            })
            .min_by_key(|field| field.len())
    };
    let shorter_form = || {
        allowed
            .iter()
            .filter(|field| {
                let field = normalize_field(field);
                field.len() >= 3 && (normalized.starts_with(&field) || normalized.ends_with(&field))
            })
            .max_by_key(|field| field.len())
    };
    let near_spelling = || {
        allowed
            .iter()
            .map(|field| (field, edit_distance(&normalize_field(field), &normalized)))
            .filter(|(_, distance)| *distance <= (normalized.chars().count() / 4).max(1))
            .min_by_key(|(_, distance)| *distance)
            .map(|(field, _)| field)
    };
    same_name
        .or_else(longer_form)
        .or_else(shorter_form)
        .or_else(near_spelling)
        .map(String::as_str)
}

fn normalize_field(name: &str) -> String {
    name.chars()
        .filter(|character| character.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn edit_distance(left: &str, right: &str) -> usize {
    let right: Vec<char> = right.chars().collect();
    let mut previous: Vec<usize> = (0..=right.len()).collect();
    for (row, left_char) in left.chars().enumerate() {
        let mut current = vec![row + 1];
        for (column, right_char) in right.iter().enumerate() {
            let substitution = previous[column] + usize::from(left_char != *right_char);
            current.push(
                substitution
                    .min(previous[column + 1] + 1)
                    .min(current[column] + 1),
            );
        }
        previous = current;
    }
    previous[right.len()]
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn bash_schema() -> Value {
        json!({
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "integer", "minimum": 1},
            },
            "required": ["command"],
            "additionalProperties": false,
        })
    }

    #[test]
    fn unknown_fields_list_the_allowed_fields_and_the_likely_one() {
        let error = validate_json_schema(
            &bash_schema(),
            &json!({"command": "sleep 5", "timeout": 30}),
        )
        .unwrap_err();
        assert_eq!(
            error,
            "Additional properties are not allowed ('timeout' was unexpected). Allowed fields: command, timeout_seconds. Use \"timeout_seconds\" instead of \"timeout\"."
        );
    }

    #[test]
    fn unknown_fields_without_a_clear_match_only_list_the_allowed_fields() {
        let error =
            validate_json_schema(&bash_schema(), &json!({"command": "ls", "verbose": true}))
                .unwrap_err();
        assert!(error.ends_with("Allowed fields: command, timeout_seconds."));
    }

    #[test]
    fn nested_errors_name_the_field() {
        let schema = json!({
            "type": "object",
            "properties": {
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"old_string": {"type": "string"}},
                        "additionalProperties": false,
                    },
                },
            },
        });
        let error =
            validate_json_schema(&schema, &json!({"edits": [{"old_str": "a"}]})).unwrap_err();
        assert!(error.starts_with("at /edits/0: Additional properties are not allowed"));
        assert!(error.ends_with("Use \"old_string\" instead of \"old_str\"."));
        let error = validate_json_schema(&bash_schema(), &json!({"command": 7})).unwrap_err();
        assert_eq!(error, "at /command: 7 is not of type \"string\"");
    }

    #[test]
    fn closest_field_matches_style_length_and_spelling() {
        let allowed = ["file_path", "old_string", "new_string", "replace_all"]
            .map(String::from)
            .to_vec();
        assert_eq!(closest_field("filePath", &allowed), Some("file_path"));
        assert_eq!(closest_field("path", &allowed), Some("file_path"));
        assert_eq!(closest_field("old_str", &allowed), Some("old_string"));
        assert_eq!(closest_field("replace_al", &allowed), Some("replace_all"));
        let read_file = ["path", "offset", "limit"].map(String::from).to_vec();
        assert_eq!(closest_field("file_path", &read_file), Some("path"));
        assert_eq!(closest_field("start_offset", &read_file), Some("offset"));
        assert_eq!(closest_field("content", &allowed), None);
        assert_eq!(closest_field("_", &allowed), None);
    }
}
