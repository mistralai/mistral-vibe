//! Readable JSON Schema validation messages for tool arguments.

use crate::core::helpers::edit_distance;
use jsonschema::error::ValidationErrorKind;
use serde_json::Value;

/// Most allowed field names listed back to the model for an unknown field.
const LISTED_FIELDS: usize = 24;

/// The validator's message, made actionable for the model: where in the
/// arguments the problem is and, for an unknown field, which fields the tool
/// accepts and which one the model most likely meant.
pub(crate) fn schema_error_detail(
    schema: &Value,
    error: &jsonschema::ValidationError<'_>,
) -> String {
    let allowed = allowed_for(schema, error);
    schema_error_detail_with_fields(error, &allowed, &allowed)
}

/// [`schema_error_detail`] with the rejected value replaced by its JSON type,
/// such as `the string value is not of type "integer"`. Use it when the value
/// does not come from the reader of the message, so it may hold a secret.
pub(crate) fn schema_error_detail_without_value(
    schema: &Value,
    error: &jsonschema::ValidationError<'_>,
) -> String {
    let placeholder = format!("the {} value", json_type(error.instance()));
    let allowed = allowed_for(schema, error);
    detail_with_message(
        error,
        error.masked_with(placeholder).to_string(),
        &allowed,
        &allowed,
    )
}

/// [`schema_error_detail`] with the allowed fields and the fields to match an
/// unknown field against given by the caller, for a tool whose arguments take
/// one of several alternatives: the reported alternative can lack the field the
/// model meant. A field that a hint points to is listed after the allowed ones.
pub(crate) fn schema_error_detail_with_fields(
    error: &jsonschema::ValidationError<'_>,
    allowed: &[String],
    hint_fields: &[String],
) -> String {
    detail_with_message(error, error.to_string(), allowed, hint_fields)
}

fn allowed_for(schema: &Value, error: &jsonschema::ValidationError<'_>) -> Vec<String> {
    match error.kind() {
        ValidationErrorKind::AdditionalProperties { .. } => {
            allowed_fields(schema, error.schema_path().as_str())
        }
        _ => Vec::new(),
    }
}

fn json_type(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

fn detail_with_message(
    error: &jsonschema::ValidationError<'_>,
    mut detail: String,
    allowed: &[String],
    hint_fields: &[String],
) -> String {
    let location = error.instance_path().as_str();
    if !location.is_empty() {
        detail = format!("at {location}: {detail}");
    }
    let ValidationErrorKind::AdditionalProperties { unexpected } = error.kind() else {
        return detail;
    };
    if allowed.is_empty() {
        return detail;
    }
    let hints: Vec<(&str, &String)> = unexpected
        .iter()
        .filter_map(|name| {
            closest_field(name, hint_fields)
                .filter(|field| *field != name.as_str())
                .map(|field| (field, name))
        })
        .collect();
    let mut listed: Vec<&str> = allowed.iter().map(String::as_str).collect();
    for (field, _) in &hints {
        if !listed.contains(field) {
            listed.push(field);
        }
    }
    detail.push_str(". Allowed fields: ");
    detail.push_str(
        &listed
            .iter()
            .take(LISTED_FIELDS)
            .copied()
            .collect::<Vec<_>>()
            .join(", "),
    );
    if listed.len() > LISTED_FIELDS {
        detail.push_str(", ...");
    }
    detail.push('.');
    for (field, name) in hints {
        detail.push_str(&format!(" Use {field:?} instead of {name:?}."));
    }
    detail
}

/// The properties declared by the schema object whose `additionalProperties`
/// rejected a field.
pub(crate) fn allowed_fields(schema: &Value, keyword_location: &str) -> Vec<String> {
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn validate_json_schema(schema: &Value, value: &Value) -> Result<(), String> {
        jsonschema::draft7::options()
            .should_validate_formats(true)
            .build(schema)
            .unwrap()
            .validate(value)
            .map_err(|error| schema_error_detail(schema, &error))
    }

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
