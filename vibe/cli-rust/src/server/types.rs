//! Wire types for the Vibe app-server JSON-RPC subset used by the PoC.

use std::collections::BTreeMap;

use serde::{Deserialize, Deserializer, Serialize};
use serde_json::Value;

use super::effect::EffectEntry;
use super::images::ImageAttachment;

/// History entries any session read asks for, everywhere.
pub const HISTORY_LIMIT: u32 = 200;

/// Methods this PoC issues. Everything else on the server is intentionally unused.
pub mod method {
    pub const INITIALIZE: &str = "initialize";
    pub const INITIALIZED: &str = "initialized";
    pub const SESSION_START: &str = "session/start";
    pub const SESSION_LIST: &str = "session/list";
    pub const SESSION_READ: &str = "session/read";
    pub const SESSION_HISTORY_LIST: &str = "session/history/list";
    pub const SESSION_STOP: &str = "session/stop";
    pub const SESSION_RESUME: &str = "session/resume";
    pub const SESSION_DELETE: &str = "session/delete";
    pub const SESSION_READY_WAIT: &str = "session/ready/wait";
    pub const RUNTIME_READ: &str = "runtime/read";
    pub const CONFIG_READ: &str = "config/read";
    pub const WORKSPACE_PROMPT_PREPARE: &str = "workspace/prompt/prepare";
    /// Whether the cwd is trusted, plus what the gate must show when it is not.
    pub const WORKSPACE_TRUST_STATUS: &str = "workspace/trust/status";
    /// The answer the user gave the gate.
    pub const WORKSPACE_TRUST_DECISION: &str = "workspace/trust/decision";
    /// Untrusted local config folders the client warns about once per folder.
    pub const WORKSPACE_TRUST_UNTRUSTED_CONFIG: &str = "workspace/trust/untrustedConfig";
    /// Queue a user turn (Python `session.enqueue_turn`).
    pub const TURN_ENQUEUE: &str = "session/turn/enqueue";
    /// Start a turn immediately, bypassing the queue (Python `session.act`).
    pub const TURN_START: &str = "turn/start";
    /// Drop a queued prompt that has not been promoted yet (Python `remove_queued_turn`).
    pub const TURN_QUEUE_REMOVE: &str = "session/turn/queue/remove";
    /// Rewrite a queued prompt in place, keeping its id and FIFO position.
    pub const TURN_QUEUE_REPLACE: &str = "session/turn/queue/replace";
    /// Atomically transfer an accepted queue item into the active turn.
    pub const TURN_QUEUE_STEER: &str = "session/turn/queue/steer";
    /// Release a queue the server paused, e.g. after an interrupt.
    pub const TURN_QUEUE_RESUME: &str = "session/turn/queue/resume";
    /// Start a fresh conversation while retaining the current session attachment.
    pub const SESSION_HISTORY_CLEAR: &str = "session/history/clear";
    /// Switch the session's agent (Python `AgentResource.switch`).
    pub const SESSION_AGENT_UPDATE: &str = "session/agent/update";
    /// Whether rewinding to a history entry would restore files on disk.
    pub const SESSION_REWIND_READ: &str = "session/rewind/read";
    /// Truncate the conversation at a user entry, optionally restoring files.
    pub const SESSION_REWIND: &str = "session/rewind";
    /// Cancel the in-progress turn (Python `session.interrupt`).
    pub const TURN_INTERRUPT: &str = "turn/interrupt";
    /// Compact conversation history by summarizing (Python `session/compact`).
    pub const SESSION_COMPACT: &str = "session/compact";
    /// Run or interrupt a user-entered `!` command.
    pub const SESSION_SHELL_COMMAND: &str = "session/shellCommand";
    /// Read the config field views the `/config` settings screen renders.
    pub const CONFIG_FIELDS_READ: &str = "config/fields/read";
    /// Persist a config change (e.g. the theme picker sets `/theme`).
    pub const CONFIG_WRITE: &str = "config/write";
    /// Re-read config and runtime after a change (Python `_reload_config`).
    pub const CONFIG_RELOAD: &str = "config/reload";
    pub const SESSION_LOG_READ: &str = "session/log/read";
    pub const SESSION_RENAME: &str = "session/rename";
    pub const IDENTITY_READ: &str = "identity/read";
    pub const ACCOUNT_READ: &str = "account/read";
    /// MCP servers and workspace connectors browsed by `/mcp`.
    pub const MCP_READ: &str = "mcp_catalog/read";
    pub const MCP_REFRESH: &str = "mcp_catalog/refresh";
    pub const MCP_TOGGLE: &str = "mcp_catalog/toggle";
    /// Connectors are toggled on their own catalog, keyed by alias.
    pub const CONNECTOR_TOGGLE: &str = "connector_catalog/toggle";
    pub const MCP_ADD: &str = "mcp_catalog/add";
    pub const MCP_REMOVE: &str = "mcp_catalog/remove";
    pub const MCP_LOGIN: &str = "mcp_catalog/login";
    pub const MCP_LOGOUT: &str = "mcp_catalog/logout";
    /// The OAuth URL of a workspace connector, or none when it needs no auth.
    pub const CONNECTOR_AUTH_READ: &str = "connectors/auth/read";
    /// Re-discover the tools of one connector after its browser sign-in.
    pub const CONNECTOR_REFRESH: &str = "connectors/refresh";
    /// Client -> server: the semantic answer to a `callback/call` request.
    pub const CALLBACK_RESULT: &str = "callback/result";
    /// Ask the server-owned feedback resource whether the prompt is eligible.
    pub const FEEDBACK_SHOULD_SHOW: &str = "feedback/shouldShow";
    /// Persist that feedback was asked for, given, or snoozed.
    pub const FEEDBACK_RECORD: &str = "feedback/record";
    /// Record client-side feature telemetry through the server-owned resource.
    pub const TELEMETRY_RECORD: &str = "telemetry/record";
    /// Ask the narration resource for the spoken summary of a finished turn.
    pub const NARRATION_SUMMARIZE: &str = "narration/summarize";
}

/// Callback kinds the client advertises at `initialize` and knows how to answer.
pub const CALLBACK_KINDS: [&str; 2] = ["approval", "user_input"];

/// Server -> client request methods this PoC answers.
pub mod server_method {
    /// The server asks the client to resolve an open callback (e.g. approval).
    pub const CALLBACK_CALL: &str = "callback/call";
}

/// Server -> client notification methods this PoC reduces.
pub mod notification {
    pub const SERVER_DISCONNECTED: &str = "__server_disconnected";
    /// Client-synthesized: a `callback/result` denial was rejected by the server
    /// (sent by the deny watcher, reduced by the headless consumer).
    pub const CALLBACK_RESULT_FAILED: &str = "__callback_result_failed";
    pub const RUNTIME_UPDATED: &str = "runtime/updated";
    pub const SESSION_SNAPSHOT: &str = "session/snapshot";
    pub const SESSION_UPDATED: &str = "session/updated";
    pub const HISTORY_ENTRY_ADDED: &str = "history/entryAdded";
    pub const HISTORY_ENTRY_UPDATED: &str = "history/entryUpdated";
    pub const TURN_STARTED: &str = "turn/started";
    pub const TURN_COMPLETED: &str = "turn/completed";
    /// The authoritative prompt queue after every accept, promotion or removal.
    pub const TURN_QUEUE_UPDATED: &str = "turn/queueUpdated";
    pub const SESSION_STATS_UPDATED: &str = "session/statsUpdated";
    /// The server finished compacting the conversation history.
    pub const SESSION_COMPACTED: &str = "session/compacted";
    /// Operational warning pushed by the server (Python `ServerWarning`).
    pub const WARNING: &str = "warning";
    /// Operational error pushed by the server (Python `ServerError`).
    pub const ERROR: &str = "error";
    /// A turn is retrying after a transient failure (Python `TurnRetrying`).
    pub const TURN_RETRYING: &str = "turn/retrying";
    /// URL to open while an `mcp_catalog/login` request is in flight.
    pub const MCP_AUTH_URL: &str = "mcp_catalog/authUrl";
    /// Compatibility duplicate the catalog publishes alongside the canonical
    /// notification; consumed without acting on it, like the Python client.
    pub const MCP_AUTH_URL_LEGACY: &str = "mcp/authUrl";
}

// JSON-RPC envelopes.

/// Outgoing request. `id` absent => notification (no response expected).
#[derive(Debug, Serialize)]
pub struct Request<'a> {
    pub jsonrpc: &'static str,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<u64>,
    pub method: &'a str,
    pub params: Value,
}

impl<'a> Request<'a> {
    pub fn call(id: u64, method: &'a str, params: Value) -> Self {
        Self {
            jsonrpc: "2.0",
            id: Some(id),
            method,
            params,
        }
    }

    pub fn notify(method: &'a str, params: Value) -> Self {
        Self {
            jsonrpc: "2.0",
            id: None,
            method,
            params,
        }
    }
}

/// Any inbound line from the server, discriminated after parse by id/method.
#[derive(Debug, Deserialize)]
pub struct Incoming {
    #[serde(default)]
    pub id: Option<u64>,
    #[serde(default)]
    pub method: Option<String>,
    #[serde(default)]
    pub params: Option<Value>,
    #[serde(default)]
    pub result: Option<Value>,
    #[serde(default)]
    pub error: Option<RpcError>,
}

#[derive(Debug, Deserialize)]
pub struct RpcError {
    /// The app-server sends `ProtocolErrorCode` strings (`"conflict"`), plain
    /// JSON-RPC peers send integers; a rejected code would drop the whole frame
    /// and hang the caller, so it stays untyped.
    #[serde(default)]
    pub code: Value,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub data: Option<Value>,
}

impl RpcError {
    /// The code as written, without JSON quoting.
    pub fn code(&self) -> String {
        match self.code.as_str() {
            Some(text) => text.to_owned(),
            None => self.code.to_string(),
        }
    }
}

// Request params (client -> server). camelCase, no extra fields.

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct InitializeParams {
    pub client_info: ClientInfo,
    pub capabilities: ClientCapabilities,
}

#[derive(Debug, Serialize)]
pub struct ClientInfo {
    pub name: String,
    /// Python `ClientInfo.entrypoint`: `programmatic` in headless mode, `cli`
    /// for the TUI. Drives analytics launch-context (ADR 0008).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub entrypoint: Option<String>,
    pub version: String,
}

/// Advertises the callback kinds the client answers (`approval`, `user_input`).
#[derive(Debug, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ClientCapabilities {
    pub callback_kinds: Vec<String>,
    pub client_tools: Vec<String>,
    pub disabled_notifications: Vec<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionStartParams {
    pub agent_config: AgentConfig,
    pub history_limit: u32,
}

/// Fields of the Python `AgentConfig` / `SessionOptions` the client sends at
/// `session/start`. Only `cwd` is set in interactive mode; headless mode sets
/// the budget and tool-filter fields.
#[derive(Debug, Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cwd: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent: Option<String>,
    #[serde(skip_serializing_if = "skip_default_bool")]
    pub auto_approve: bool,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub enabled_tools: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub disabled_tools: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_turns: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_price: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_session_tokens: Option<u64>,
    #[serde(skip_serializing_if = "skip_default_bool", default)]
    pub headless: bool,
    #[serde(skip_serializing_if = "skip_default_bool", default)]
    pub trust_workspace: bool,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub workspace_roots: Vec<String>,
}

fn skip_default_bool(b: &bool) -> bool {
    !*b
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeReadParams {
    pub session_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnEnqueueParams {
    pub idempotency_key: String,
    pub session_id: String,
    pub entries: Vec<TurnInputEntry>,
}

/// `turn/start` params (Python `TurnStartParams`): start a turn immediately,
/// bypassing the queue. `injected` suppresses the user-message projection.
/// Null fields match Python's wire format (protocol.py:1852).
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnStartParams {
    pub idempotency_key: Option<String>,
    pub session_id: String,
    pub message: Vec<ContentBlock>,
    pub injected: bool,
    pub client_user_message_id: Option<String>,
    pub auto_title: Option<String>,
    pub user_display_content: Option<serde_json::Value>,
    pub mention_stats: Option<serde_json::Value>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnQueueReplaceParams {
    pub idempotency_key: String,
    pub session_id: String,
    pub queue_item_id: String,
    pub entries: Vec<TurnInputEntry>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnQueueRemoveParams {
    pub session_id: String,
    pub queue_item_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnQueueSteerParams {
    pub session_id: String,
    pub queue_item_id: String,
    pub expected_turn_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnInputEntry {
    pub annotations: BTreeMap<String, String>,
    pub content: Vec<ContentBlock>,
    /// Client-assigned id echoed back as the user entry's `id` for dedupe.
    pub entry_id: String,
    pub role: &'static str,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum ContentBlock {
    Text {
        text: String,
    },
    Image {
        uri: String,
        #[serde(rename = "mediaType", default)]
        media_type: Option<String>,
        #[serde(rename = "altText", default)]
        alt_text: Option<String>,
    },
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TurnInterruptParams {
    pub session_id: String,
    pub expected_turn_id: String,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ShellCommandAction {
    Run,
    Interrupt,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionShellCommandParams {
    pub session_id: String,
    pub command: Option<String>,
    pub cwd: Option<String>,
    pub timeout_seconds: Option<f64>,
    pub operation_id: Option<String>,
    pub action: ShellCommandAction,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionHistoryClearParams {
    pub session_id: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FeedbackShouldShowParams {
    pub session_id: String,
    pub pending_user_messages: u32,
}

#[derive(Clone, Copy, Debug, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum FeedbackAction {
    Asked,
    Given,
    Snoozed,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FeedbackRecordParams {
    pub session_id: String,
    pub action: FeedbackAction,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct TelemetryRecordParams {
    pub session_id: String,
    pub name: String,
    pub properties: BTreeMap<String, Value>,
    pub correlate_last_request: bool,
}

/// Python `NarrationSummarizeParams`; the optional fields go out as `null`,
/// like the Python protocol model serializes them.
#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct NarrationSummarizeParams {
    pub session_id: String,
    pub user_message: String,
    pub assistant_text: String,
    pub error: Option<String>,
    pub message_id: Option<String>,
}

/// Python `NarrationSummarizeResponse`: `summary` is absent or null when the
/// server has nothing to speak.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct NarrationSummarizeResponse {
    #[serde(default)]
    pub summary: Option<String>,
}

// Response / notification params (server -> client). Parsed leniently.

/// Subset of `PublicSessionState` we read; history stays as raw JSON.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicSessionState {
    pub event_id: u64,
    pub session: PublicSession,
    #[serde(default)]
    pub history: Option<Vec<Value>>,
    #[serde(default)]
    pub turns: Option<Vec<Value>>,
    #[serde(default)]
    pub turn_queue: Option<Value>,
    /// Provider retry in flight (Python `PublicRetryState`); `None` when idle.
    #[serde(default)]
    pub retrying: Option<PublicRetryState>,
}

/// A provider retry in progress: which turn, why, and the transport detail.
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicRetryState {
    pub turn_id: String,
    pub category: String,
    pub detail: String,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TokenUsage {
    #[serde(default)]
    pub input_tokens: u64,
    #[serde(default)]
    pub output_tokens: u64,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct PublicSession {
    pub id: String,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub cwd: Option<String>,
    #[serde(default)]
    pub token_usage: Option<TokenUsage>,
}

/// A history entry, typed by its `type` discriminator (Python `PublicHistoryEntry`).
#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum HistoryEntry {
    Message(MessageEntry),
    Reasoning(ReasoningEntry),
    Effect(Box<EffectEntry>),
    Callback(CallbackEntry),
    Checkpoint(CheckpointEntry),
    Notice(NoticeEntry),
    /// Client-owned marker mounted after a cancelled turn; no Python equivalent.
    Interrupt(InterruptEntry),
    #[serde(other)]
    Unknown,
}

impl HistoryEntry {
    /// Parse a stored entry `Value`; unknown or invalid shapes render as `Unknown`.
    pub fn from_value(value: &Value) -> Self {
        serde_json::from_value(value.clone()).unwrap_or(Self::Unknown)
    }

    /// Python `generation_status == "in_progress"`: the entry is still streaming.
    pub fn in_progress(&self) -> bool {
        matches!(self.generation_status(), Some("in_progress"))
    }

    fn generation_status(&self) -> Option<&str> {
        match self {
            Self::Message(e) => e.generation_status.as_deref(),
            Self::Reasoning(e) => e.generation_status.as_deref(),
            Self::Effect(e) => e.generation_status.as_deref(),
            Self::Callback(e) => e.generation_status.as_deref(),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageEntry {
    #[serde(default)]
    pub generation_status: Option<String>,
    #[serde(default = "assistant_role")]
    pub role: String,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default, deserialize_with = "deserialize_message_content")]
    pub content: Vec<MessageContent>,
}

fn assistant_role() -> String {
    "assistant".into()
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "lowercase")]
pub enum MessageContent {
    Text {
        text: String,
    },
    Image {
        attachment: ImageAttachment,
    },
    #[serde(other)]
    Other,
}

fn deserialize_message_content<'de, D>(deserializer: D) -> Result<Vec<MessageContent>, D::Error>
where
    D: Deserializer<'de>,
{
    let value = Value::deserialize(deserializer)?;
    Ok(value
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|item| serde_json::from_value(item.clone()).ok())
        .collect())
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ReasoningEntry {
    #[serde(default)]
    pub generation_status: Option<String>,
    #[serde(default)]
    pub text: String,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CallbackEntry {
    #[serde(default)]
    pub generation_status: Option<String>,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub state: Option<CallbackState>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CallbackState {
    #[serde(default)]
    pub status: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct CheckpointEntry {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub message: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NoticeEntry {
    #[serde(default)]
    pub level: String,
    #[serde(default)]
    pub message: String,
    #[serde(default)]
    pub detail: Option<NoticeDetail>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct NoticeDetail {
    #[serde(default)]
    pub kind: Option<String>,
    #[serde(default)]
    pub content: Option<String>,
    /// `HookNoticeDetail`: where the hook runs and which tool call it pairs with.
    #[serde(default)]
    pub scope: Option<String>,
    #[serde(default, rename = "toolCallId")]
    pub tool_call_id: Option<String>,
    #[serde(default, rename = "toolName")]
    pub tool_name: Option<String>,
    #[serde(default, rename = "hookName")]
    pub hook_name: Option<String>,
    /// `ok` / `warning` / `error`; absent means warning.
    #[serde(default)]
    pub status: Option<String>,
}

/// `workspace/trust/untrustedConfig` response (Python `WorkspaceUntrustedConfigResponse`).
#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkspaceUntrustedConfigResponse {
    #[serde(default)]
    pub dirs: Vec<String>,
    #[serde(default)]
    pub settings_path: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct InterruptEntry {}

/// `session/start` result envelope.
#[derive(Debug, Deserialize)]
pub struct SessionStartResponse {
    pub state: PublicSessionState,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct FeedbackShouldShowResponse {
    pub show: bool,
    #[serde(default)]
    pub snooze_duration_seconds: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn narration_summarize_params_go_out_camelcase() {
        let params = NarrationSummarizeParams {
            session_id: "s1".into(),
            user_message: "hello".into(),
            assistant_text: "hi there".into(),
            error: None,
            message_id: Some("m1".into()),
        };
        let json = serde_json::to_value(&params).unwrap();
        assert_eq!(json["sessionId"], "s1");
        assert_eq!(json["userMessage"], "hello");
        assert_eq!(json["assistantText"], "hi there");
        assert_eq!(json["error"], Value::Null);
        assert_eq!(json["messageId"], "m1");
    }

    #[test]
    fn narration_summarize_response_tolerates_null_and_missing_summary() {
        let text = r#"{"summary":"all done"}"#;
        let summary = serde_json::from_str::<NarrationSummarizeResponse>(text)
            .unwrap()
            .summary;
        assert_eq!(summary.as_deref(), Some("all done"));
        let none = serde_json::from_str::<NarrationSummarizeResponse>(r#"{"summary":null}"#)
            .unwrap()
            .summary;
        assert!(none.is_none());
        let missing = serde_json::from_str::<NarrationSummarizeResponse>(r#"{}"#).unwrap();
        assert!(missing.summary.is_none());
    }
}
