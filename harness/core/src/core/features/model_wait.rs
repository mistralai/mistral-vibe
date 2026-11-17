//! Standalone model waiting; the Runtime owns waiting on committed input.

use serde::{Deserialize, Serialize};
use serde_json::{Value, json};

use crate::core::config::ToolSettings;
use crate::core::error::CoreError;
use crate::core::step_protocol::ToolDefinition;

/// Runtime support declaration for waiting on committed Session input.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "mode", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Mode {
    #[default]
    Disabled,
    Enabled {
        /// The Host may deliver input to this Session from outside it, for
        /// example messages from a parent agent.
        #[serde(default, skip_serializing_if = "std::ops::Not::not")]
        external_input: bool,
    },
}

/// Offers `wait` only when the Runtime supports it and the Session can receive
/// input that is worth waiting for: subagent results, background-process
/// completions, or input the Host delivers from outside the Session. The tool set stays fixed for each settings
/// revision; it never depends on whether such work is running right now.
pub(crate) fn is_offered(tools: &ToolSettings) -> bool {
    let Mode::Enabled { external_input } = tools.model_wait else {
        return false;
    };
    external_input || tools.subagents.is_enabled() || tools.background_processes.is_enabled()
}

pub(crate) fn default_max_wait_ms() -> u64 {
    3_600_000
}

pub(crate) fn tool(max_wait_ms: u64, description: &str) -> ToolDefinition {
    ToolDefinition {
        name: "wait".to_string(),
        description: description.to_owned(),
        parameters: json!({
            "type": "object", "additionalProperties": false,
            "properties": {"timeout_ms": {"type": "integer", "minimum": 1, "maximum": max_wait_ms}},
            "required": ["timeout_ms"],
        }),
    }
}

pub(crate) fn runtime_input_schema(max_wait_ms: u64) -> Value {
    let mut schema = tool(max_wait_ms, "").parameters;
    schema["properties"]["deadline_unix_ms"] = json!({"type": "integer", "minimum": 0});
    schema["required"] = json!(["timeout_ms", "deadline_unix_ms"]);
    schema
}

pub(crate) fn output_schema() -> Value {
    json!({
        "type": "object", "additionalProperties": false,
        "properties": {"reason": {"enum": ["input_available", "timeout"]}},
        "required": ["reason"],
    })
}

pub(crate) fn runtime_arguments(arguments: Value, time_unix_ms: u64) -> Result<Value, CoreError> {
    #[derive(Deserialize)]
    struct Request {
        #[serde(deserialize_with = "integral_u64")]
        timeout_ms: u64,
    }
    let request: Request = serde_json::from_value(arguments)
        .map_err(|_| CoreError::invalid_command("timeout_ms must be a positive integer"))?;
    Ok(json!({
        "timeout_ms": request.timeout_ms,
        "deadline_unix_ms": time_unix_ms.saturating_add(request.timeout_ms),
    }))
}

/// A pre-tool Hook may rewrite both Runtime arguments, and the schema bounds
/// only `timeout_ms`. Keep the deadline within the effective `timeout_ms` of
/// the original start, so a Hook can shorten a wait but never extend it past
/// `max_wait_ms`.
///
/// The outer error is a Core bug, since Core wrote the original arguments. The
/// inner error says why the hook's arguments cannot be read even though the
/// schema accepted them, such as a deadline beyond `u64`.
pub(crate) fn clamp_hook_arguments(
    original: &Value,
    effective: Value,
) -> Result<Result<Value, String>, CoreError> {
    #[derive(Deserialize)]
    struct RuntimeRequest {
        #[serde(deserialize_with = "integral_u64")]
        timeout_ms: u64,
        #[serde(deserialize_with = "integral_u64")]
        deadline_unix_ms: u64,
    }
    let original = serde_json::from_value::<RuntimeRequest>(original.clone())
        .map_err(|error| CoreError::invariant(format!("invalid wait arguments: {error}")))?;
    let effective = match serde_json::from_value::<RuntimeRequest>(effective) {
        Ok(effective) => effective,
        Err(_) => {
            return Ok(Err(format!(
                "`timeout_ms` and `deadline_unix_ms` must be integers from 0 to {}",
                u64::MAX
            )));
        }
    };
    let started_at_unix_ms = original
        .deadline_unix_ms
        .saturating_sub(original.timeout_ms);
    Ok(Ok(json!({
        "timeout_ms": effective.timeout_ms,
        "deadline_unix_ms": effective
            .deadline_unix_ms
            .min(started_at_unix_ms.saturating_add(effective.timeout_ms)),
    })))
}

/// 2^64, the first value past `u64::MAX`. `u64::MAX as f64` rounds to it, so
/// a range check must exclude it.
const U64_END: f64 = 18_446_744_073_709_551_616.0;

/// A JSON Schema `integer` that is not negative. JSON Schema counts a number
/// with a zero fractional part, such as `500.0`, as an integer, so a value the
/// schema accepted must also parse here.
fn integral_u64<'de, D>(deserializer: D) -> Result<u64, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let number = serde_json::Number::deserialize(deserializer)?;
    if let Some(value) = number.as_u64() {
        return Ok(value);
    }
    number
        .as_f64()
        .filter(|value| value.fract() == 0.0 && *value >= 0.0 && *value < U64_END)
        .map(|value| value as u64)
        .ok_or_else(|| {
            serde::de::Error::custom(format!("{number} is not an integer from 0 to {}", u64::MAX))
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn original() -> Value {
        json!({"timeout_ms": 30_000, "deadline_unix_ms": 1_700_000_030_000_u64})
    }

    #[test]
    fn a_hook_deadline_of_two_to_the_64_is_unreadable() {
        let effective =
            json!({"timeout_ms": 10_000, "deadline_unix_ms": 18_446_744_073_709_551_616.0});

        let clamped = clamp_hook_arguments(&original(), effective).unwrap();

        assert!(clamped.is_err(), "{clamped:?}");
    }

    #[test]
    fn a_hook_deadline_just_below_two_to_the_64_is_read_and_clamped() {
        let below = f64::from_bits(U64_END.to_bits() - 1);
        let effective = json!({"timeout_ms": 10_000, "deadline_unix_ms": below});

        let clamped = clamp_hook_arguments(&original(), effective).unwrap();

        assert_eq!(
            clamped,
            Ok(json!({"timeout_ms": 10_000, "deadline_unix_ms": 1_700_000_010_000_u64}))
        );
    }
}
