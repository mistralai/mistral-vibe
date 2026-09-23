//! Wire-contract skew tolerance and request round-trips (ADR-0014).

use serde_json::{json, Value};
use vibe_rs::server::{
    method, notification, AgentSafety, AgentSummary, AgentSwitchParams, AgentType,
    ApprovalCallback, ApprovalDecisionType, ContentBlock, FeedbackAction, FeedbackRecordParams,
    FeedbackShouldShowParams, FeedbackShouldShowResponse, HistoryEntry, Incoming, InitializeParams,
    MCPSourceKind, MCPSourceStatus, MCPState, Request, SessionStartParams, SessionStartResponse,
    TelemetryRecordParams, TurnEnqueueParams, TurnInputEntry, UserAnswer, UserQuestionRequest,
    UserQuestionResult, WorkspaceTrustDetails, WorkspaceTrustStatusResponse, HISTORY_LIMIT,
};

#[test]
fn history_limit_matches_the_python_default() {
    assert_eq!(HISTORY_LIMIT, 200);
}

#[test]
fn method_names_pin_the_wire_contract() {
    assert_eq!(method::SESSION_START, "session/start");
    assert_eq!(method::TURN_ENQUEUE, "session/turn/enqueue");
    assert_eq!(method::TURN_QUEUE_REPLACE, "session/turn/queue/replace");
    assert_eq!(method::TURN_INTERRUPT, "turn/interrupt");
    assert_eq!(method::CALLBACK_RESULT, "callback/result");
    assert_eq!(method::SESSION_AGENT_UPDATE, "session/agent/update");
    assert_eq!(method::MCP_READ, "mcp_catalog/read");
    assert_eq!(notification::HISTORY_ENTRY_ADDED, "history/entryAdded");
    assert_eq!(notification::HISTORY_ENTRY_UPDATED, "history/entryUpdated");
    assert_eq!(notification::TURN_QUEUE_UPDATED, "turn/queueUpdated");
    assert_eq!(notification::SESSION_STATS_UPDATED, "session/statsUpdated");
    assert_eq!(notification::MCP_AUTH_URL, "mcp_catalog/authUrl");
    assert_eq!(notification::MCP_AUTH_URL_LEGACY, "mcp/authUrl");
}

#[test]
fn incoming_frames_parse_with_missing_and_extra_fields() {
    let bare: Incoming = serde_json::from_str(r#"{"jsonrpc":"2.0"}"#).unwrap();
    assert_eq!(bare.id, None);
    assert_eq!(bare.method, None);
    assert_eq!(bare.params, None);
    assert!(bare.result.is_none());
    assert!(bare.error.is_none());

    let loaded: Incoming = serde_json::from_str(
        r#"{"jsonrpc":"2.0","id":3,"method":"history/entryAdded",
            "params":{"unknownFutureField":true},"extraEnvelopeField":"x"}"#,
    )
    .unwrap();
    assert_eq!(loaded.id, Some(3));
    assert_eq!(loaded.method.as_deref(), Some("history/entryAdded"));
    assert!(loaded.params.is_some());
}

#[test]
fn error_codes_accept_strings_and_numbers_with_extra_fields() {
    let string: Incoming =
        serde_json::from_str(r#"{"id":1,"error":{"code":"conflict","message":"m"}}"#).unwrap();
    let numeric: Incoming =
        serde_json::from_str(r#"{"id":2,"error":{"code":-32000,"message":"m","data":{"a":1}}}"#)
            .unwrap();
    assert_eq!(string.error.unwrap().code(), "conflict");
    assert_eq!(numeric.error.unwrap().code(), "-32000");
}

#[test]
fn requests_serialize_with_and_without_an_id() {
    let call = Request::call(7, method::SESSION_START, json!({"x": 1}));
    let call: Value = serde_json::to_value(&call).unwrap();
    assert_eq!(call["jsonrpc"], "2.0");
    assert_eq!(call["id"], 7);
    assert_eq!(call["method"], "session/start");

    let notify = Request::notify("initialized", json!({}));
    let notify: Value = serde_json::to_value(&notify).unwrap();
    assert!(notify.get("id").is_none());
    assert_eq!(notify["method"], "initialized");
}

#[test]
fn initialize_params_serialize_camel_case() {
    let params = InitializeParams {
        client_info: vibe_rs::server::ClientInfo {
            name: "vibe-rs".into(),
            entrypoint: Some("cli".into()),
            version: "1.0".into(),
        },
        capabilities: Default::default(),
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert_eq!(value["clientInfo"]["name"], "vibe-rs");
    assert_eq!(value["clientInfo"]["entrypoint"], "cli");
    assert_eq!(value["capabilities"]["callbackKinds"], json!([]));
}

#[test]
fn turn_enqueue_params_serialize_entries_and_content_blocks() {
    let params = TurnEnqueueParams {
        idempotency_key: "key-1".into(),
        session_id: "sess".into(),
        entries: vec![TurnInputEntry {
            annotations: [("intent".to_string(), "chat".to_string())].into(),
            content: vec![ContentBlock::Text {
                text: "hello".into(),
            }],
            entry_id: "e1".into(),
            role: "user",
        }],
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    let entry = &value["entries"][0];
    assert_eq!(value["idempotencyKey"], "key-1");
    assert_eq!(value["sessionId"], "sess");
    assert_eq!(entry["entryId"], "e1");
    assert_eq!(entry["role"], "user");
    assert_eq!(entry["annotations"]["intent"], "chat");
    assert_eq!(entry["content"][0]["type"], "text");
    assert_eq!(entry["content"][0]["text"], "hello");
}

#[test]
fn feedback_and_telemetry_params_serialize_camel_case() {
    let feedback: Value = serde_json::to_value(&FeedbackShouldShowParams {
        session_id: "s".into(),
        pending_user_messages: 2,
    })
    .unwrap();
    assert_eq!(feedback["pendingUserMessages"], 2);

    let record: Value = serde_json::to_value(&FeedbackRecordParams {
        session_id: "s".into(),
        action: FeedbackAction::Snoozed,
    })
    .unwrap();
    assert_eq!(record["sessionId"], "s");
    assert_eq!(record["action"], "snoozed");

    let telemetry: Value = serde_json::to_value(&TelemetryRecordParams {
        session_id: "s".into(),
        name: "event".into(),
        properties: [("k".to_string(), json!(1))].into(),
        correlate_last_request: true,
    })
    .unwrap();
    assert_eq!(telemetry["correlateLastRequest"], true);
    assert_eq!(telemetry["properties"]["k"], 1);
}

#[test]
fn session_start_params_serialize_history_limit() {
    let params = SessionStartParams {
        agent_config: Default::default(),
        history_limit: HISTORY_LIMIT,
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert_eq!(value["historyLimit"], 200);
}

#[test]
fn agent_switch_params_serialize_camel_case() {
    let params = AgentSwitchParams {
        session_id: "s".into(),
        agent_name: "code".into(),
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert_eq!(value["sessionId"], "s");
    assert_eq!(value["agentName"], "code");
}

#[test]
fn approval_decisions_serialize_snake_case() {
    let value: Value = serde_json::to_value(ApprovalDecisionType::ApproveForSession).unwrap();
    assert_eq!(value, "approve_for_session");
    let deny: Value = serde_json::to_value(ApprovalDecisionType::Deny).unwrap();
    assert_eq!(deny, "deny");
}

#[test]
fn session_state_parses_with_missing_optional_fields_and_extras() {
    let response: SessionStartResponse = serde_json::from_value(json!({
        "state": {
            "eventId": 9,
            "session": {"id": "abc", "futureField": true},
            "history": [
                {"id": "m1", "type": "message", "role": "user", "content": [],
                 "extra": 1}
            ],
            "newServerField": [1, 2]
        }
    }))
    .unwrap();
    let state = response.state;
    assert_eq!(state.event_id, 9);
    assert_eq!(state.session.id, "abc");
    assert!(state.session.title.is_none());
    assert_eq!(state.history.as_ref().unwrap().len(), 1);

    let bare: SessionStartResponse =
        serde_json::from_value(json!({"state": {"eventId": 0, "session": {"id": "x"}}})).unwrap();
    assert!(bare.state.history.is_none());
}

#[test]
fn history_entries_deserialize_leniently() {
    let message = HistoryEntry::from_value(&json!({"type": "message"}));
    let reasoning = HistoryEntry::from_value(&json!({"type": "reasoning", "extra": 1}));
    let effect = HistoryEntry::from_value(&json!({"type": "effect"}));
    let callback = HistoryEntry::from_value(&json!({"type": "callback"}));
    let checkpoint = HistoryEntry::from_value(&json!({"type": "checkpoint"}));
    let notice = HistoryEntry::from_value(&json!({"type": "notice"}));
    let interrupt = HistoryEntry::from_value(&json!({"type": "interrupt"}));
    let unknown = HistoryEntry::from_value(&json!({"type": "hologram"}));
    assert!(matches!(message, HistoryEntry::Message(_)));
    assert!(matches!(reasoning, HistoryEntry::Reasoning(_)));
    assert!(matches!(effect, HistoryEntry::Effect(_)));
    assert!(matches!(callback, HistoryEntry::Callback(_)));
    assert!(matches!(checkpoint, HistoryEntry::Checkpoint(_)));
    assert!(matches!(notice, HistoryEntry::Notice(_)));
    assert!(matches!(interrupt, HistoryEntry::Interrupt(_)));
    assert!(matches!(unknown, HistoryEntry::Unknown));
}

#[test]
fn approval_callbacks_default_every_field() {
    let callback: ApprovalCallback =
        serde_json::from_value(json!({"callbackId": "c1", "futureField": 1})).unwrap();
    assert_eq!(callback.callback_id, "c1");
    assert_eq!(callback.session_id, "");
    assert_eq!(callback.detail.effect.tool_name, "");
    assert!(callback.detail.required_permissions.is_empty());

    let full: ApprovalCallback = serde_json::from_value(json!({
        "callbackId": "c1",
        "sessionId": "s",
        "detail": {
            "effect": {"toolName": "shell", "kind": "file_edit",
                       "input": {"a": 1}, "display": {"statusText": "Waiting"}},
            "requiredPermissions": [{"label": "Run commands"}]
        }
    }))
    .unwrap();
    assert_eq!(full.detail.effect.display.status_text, "Waiting");
    assert_eq!(full.detail.required_permissions[0].label, "Run commands");
}

#[test]
fn agent_summaries_default_and_tolerate_unknown_enums() {
    let summary: AgentSummary =
        serde_json::from_value(json!({"name": "code", "displayName": "Code"})).unwrap();
    assert_eq!(summary.name, "code");
    assert_eq!(summary.display_name, "Code");
    assert_eq!(summary.safety, AgentSafety::Neutral);
    assert_eq!(summary.agent_type, AgentType::Agent);

    let future: AgentSummary = serde_json::from_value(json!({
        "name": "x", "safety": "galactic", "agentType": "holo", "extra": true
    }))
    .unwrap();
    assert_eq!(future.safety, AgentSafety::Neutral);
    assert_eq!(future.agent_type, AgentType::Other);

    let known: AgentSummary = serde_json::from_value(json!({
        "name": "x", "safety": "yolo", "agentType": "subagent"
    }))
    .unwrap();
    assert_eq!(known.safety, AgentSafety::Yolo);
    assert_eq!(known.agent_type, AgentType::Subagent);
}

#[test]
fn mcp_state_parses_and_derives_needs_auth_and_statuses() {
    let state: MCPState = serde_json::from_value(json!({
        "sources": [
            {"name": "beta", "kind": "server", "transport": "stdio",
             "status": "needs_auth", "tools": [{"name": "t1"}]},
            {"name": "alpha", "kind": "server", "transport": "stdio",
             "status": "connected", "error": null},
            {"name": "gh", "kind": "connector", "transport": "http",
             "status": "needs_auth"}
        ],
        "discoveryErrors": {"file": "boom"},
        "futureField": {"nested": true}
    }))
    .unwrap();
    assert_eq!(state.needs_auth(), vec!["beta"]);
    let statuses = state.statuses();
    assert_eq!(statuses.get("alpha"), Some(&"connected"));
    assert_eq!(statuses.get("beta"), Some(&"needs_auth"));
    assert!(!statuses.contains_key("gh"));
    assert_eq!(
        state.discovery_errors.get("file").map(String::as_str),
        Some("boom")
    );
    assert!(state.sources[0].tools[0].enabled);
    assert!(state.connector_error.is_none());
}

#[test]
fn mcp_source_kinds_and_statuses_round_trip_strings() {
    assert_eq!(MCPSourceKind::Server.as_str(), "server");
    assert_eq!(MCPSourceStatus::NeedsAuth.as_str(), "needs_auth");
    assert_eq!(MCPSourceStatus::Unavailable.as_str(), "unavailable");
}

#[test]
fn user_question_requests_default_missing_optionals() {
    let request: UserQuestionRequest = serde_json::from_value(json!({
        "questions": [{
            "question": "Which db?",
            "options": [{"label": "pg", "futureField": 1}],
            "multiSelect": true,
            "hideOther": true
        }],
        "futureTopLevel": 2
    }))
    .unwrap();
    let question = &request.questions[0];
    assert!(request.footer_note.is_none());
    assert_eq!(question.header, "");
    assert_eq!(question.options[0].description, "");
    assert!(question.multi_select && question.hide_other);
}

#[test]
fn user_question_results_serialize_camel_case() {
    let result = UserQuestionResult {
        answers: vec![UserAnswer {
            question: "Which db?".into(),
            answer: "pg".into(),
            is_other: true,
        }],
        cancelled: false,
    };
    assert_eq!(
        serde_json::to_string(&result).unwrap(),
        r#"{"answers":[{"question":"Which db?","answer":"pg","isOther":true}],"cancelled":false}"#
    );
}

#[test]
fn workspace_trust_responses_default_missing_details() {
    let empty: WorkspaceTrustStatusResponse = serde_json::from_value(json!({})).unwrap();
    assert!(empty.details.is_none());

    let details: WorkspaceTrustDetails = serde_json::from_value(json!({
        "cwd": "/repo", "futureField": "x"
    }))
    .unwrap();
    assert_eq!(details.cwd, "/repo");
    assert!(details.repo_root.is_none());
    assert!(details.detected_files.is_empty());
    assert!(!details.repo_explicitly_untrusted);
    assert!(details.available_decisions.is_empty());
}

#[test]
fn feedback_should_show_responses_parse_optional_snooze() {
    let response: FeedbackShouldShowResponse =
        serde_json::from_value(json!({"show": true})).unwrap();
    assert!(response.show);
    assert!(response.snooze_duration_seconds.is_none());

    let snoozed: FeedbackShouldShowResponse =
        serde_json::from_value(json!({"show": false, "snoozeDurationSeconds": 3600, "x": 1}))
            .unwrap();
    assert_eq!(snoozed.snooze_duration_seconds, Some(3600));
}
