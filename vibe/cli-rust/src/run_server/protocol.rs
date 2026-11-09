//! The `vibe run-server` wire types, validated without echoing any value.

use std::collections::BTreeMap;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::run_export::{RunError, RunOutcome, RunWarning};

pub const PROTOCOL_VERSION: i64 = 1;
pub const SERVER_NAME: &str = "vibe-run-server";

/// A request from the client; anything else on stdin is a protocol error.
pub struct Request {
    pub id: Value,
    pub method: String,
    pub params: Value,
}

impl Request {
    /// The request `value` is, or what is wrong with it, by field.
    pub fn parse(value: Value) -> Result<Self, String> {
        let Value::Object(mut message) = value else {
            return Err("a message must be a JSON object".into());
        };
        if message.get("jsonrpc") != Some(&json!("2.0")) {
            return Err("jsonrpc must be \"2.0\"".into());
        }
        let Some(Value::String(method)) = message.remove("method") else {
            return Err("vibe run-server takes requests only, with a string method".into());
        };
        let id = message.remove("id").unwrap_or(Value::Null);
        if !(id.is_string() || id.is_i64() || id.is_u64()) {
            return Err("vibe run-server takes requests only, with a string or integer id".into());
        }
        let params = match message.remove("params") {
            None | Some(Value::Null) => json!({}),
            Some(params @ Value::Object(_)) => params,
            Some(_) => return Err("params must be an object".into()),
        };
        Ok(Self { id, method, params })
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct InitializeParams {
    pub protocol_version: Option<i64>,
    pub client_info: Option<ClientInfo>,
}

#[derive(Deserialize)]
pub struct ClientInfo {
    pub name: String,
    pub version: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RunStartParams {
    pub run_id: Option<String>,
    pub prompt: Option<String>,
    pub agent_socket: Option<String>,
    pub output_dir: Option<String>,
    pub resume_session_id: Option<String>,
    pub agent: Option<String>,
    #[serde(default)]
    pub auto_approve: bool,
    pub enabled_tools: Option<Vec<String>>,
    pub disabled_tools: Option<Vec<String>>,
    pub max_turns: Option<u32>,
    pub max_tokens: Option<u64>,
    pub max_price: Option<f64>,
    pub time_limit: Option<f64>,
    pub config: Option<Map<String, Value>>,
    #[serde(default)]
    pub credentials: BTreeMap<String, String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RunStopParams {
    pub run_id: Option<String>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct EmptyParams {}

/// The `run/ended` notification's params.
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RunEnded {
    pub run_id: String,
    pub outcome: RunOutcome,
    pub exit_code: u8,
    pub session_id: Option<String>,
    pub error: Option<RunError>,
    pub export_path: Option<String>,
    pub export_error: Option<String>,
    pub export_error_code: Option<String>,
    pub warnings: Option<Vec<RunWarning>>,
}

/// A JSON-RPC error object, as the app server's `ProtocolError`.
pub struct Failure {
    pub code: &'static str,
    pub message: String,
    pub data: Value,
}

impl Failure {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            data: Value::Null,
        }
    }

    pub fn with_data(mut self, data: Value) -> Self {
        self.data = data;
        self
    }

    pub fn to_value(&self) -> Value {
        json!({"code": self.code, "message": self.message, "data": self.data})
    }
}

/// `params` as `T`, or `invalid_params` naming each field at fault, checked alone.
pub fn parse<T: DeserializeOwned>(params: &Value) -> Result<T, Failure> {
    let error = match T::deserialize(params) {
        Ok(parsed) => return Ok(parsed),
        Err(error) => error,
    };
    let mut issues: Vec<(String, String)> = params
        .as_object()
        .into_iter()
        .flatten()
        .filter_map(|(key, value)| {
            let alone = Value::Object(Map::from_iter([(key.clone(), value.clone())]));
            let error = T::deserialize(&alone).err()?;
            Some((key.clone(), scrub(&error)))
        })
        .collect();
    if issues.is_empty() {
        issues.push((String::new(), scrub(&error)));
    }
    Err(invalid_params(issues))
}

/// The failure for invalid parameters, as `(field, message)` pairs.
pub fn invalid_params(issues: Vec<(String, String)>) -> Failure {
    let issues: Vec<Value> = issues
        .into_iter()
        .map(|(field, message)| {
            let path: Vec<&str> = (!field.is_empty())
                .then_some(field.as_str())
                .into_iter()
                .collect();
            json!({"path": path, "message": message})
        })
        .collect();
    Failure::new("invalid_params", "Invalid request parameters")
        .with_data(json!({"errorCount": issues.len(), "issues": issues}))
}

/// A serde error without the value it quotes: that value may be a credential.
fn scrub(error: &serde_json::Error) -> String {
    let message = error.to_string();
    for kind in ["invalid type", "invalid value", "invalid length"] {
        if message.starts_with(kind) {
            return match message.rfind(", expected ") {
                Some(at) => format!("{kind}{}", &message[at..]),
                None => kind.to_owned(),
            };
        }
    }
    message
}
