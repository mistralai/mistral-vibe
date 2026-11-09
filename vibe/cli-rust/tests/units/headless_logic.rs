//! Tests for headless-mode logic: disabled tools, AgentConfig wiring, output formatting.
use std::ffi::OsString;
use std::path::PathBuf;

use serde_json::{json, Value};
use vibe_rs::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use vibe_rs::headless::{agent_config, build_disabled_tools, open_failure, turn_id_from};
use vibe_rs::headless_output::{last_assistant_text, turn_entries, Output};
use vibe_rs::headless_prompt::{parse_piped_prompt, PipedPrompt, MAX_STDIN_PROMPT_BYTES};
use vibe_rs::headless_trust::warning_from;
use vibe_rs::run_export::RunOutcome;
use vibe_rs::server::{AgentConfig, RpcError};
use vibe_rs::utils::paths::user_home_from;

#[test]
fn disabled_tools_appends_ask_user_question_and_cron() {
    let tools = build_disabled_tools(&[]);
    assert_eq!(
        tools,
        vec!["ask_user_question".to_owned(), "cron".to_owned()]
    );
}

#[test]
fn disabled_tools_preserves_explicit_and_dedupes() {
    let tools = build_disabled_tools(&["bash".into(), "ask_user_question".into()]);
    assert_eq!(
        tools,
        vec![
            "bash".to_owned(),
            "ask_user_question".to_owned(),
            "cron".to_owned()
        ]
    );
}

fn rpc_error(code: &str, data: Option<Value>) -> RpcError {
    serde_json::from_value(json!({"code": code, "message": "m", "data": data})).unwrap()
}

#[test]
fn a_session_that_does_not_open_is_classified_for_the_export() {
    let config = json!({"kind": "configuration"});
    assert_eq!(
        open_failure(&rpc_error("unauthorized", None)),
        RunOutcome::ConfigError
    );
    assert_eq!(
        open_failure(&rpc_error("invalid_params", Some(config))),
        RunOutcome::ConfigError
    );
    assert_eq!(
        open_failure(&rpc_error("not_found", None)),
        RunOutcome::UsageError
    );
    assert_eq!(
        open_failure(&rpc_error("invalid_params", None)),
        RunOutcome::UsageError
    );
    assert_eq!(
        open_failure(&rpc_error("internal_error", None)),
        RunOutcome::InfrastructureFailure
    );
}

#[test]
fn agent_config_serializes_headless_flag() {
    let config = AgentConfig {
        headless: true,
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json.get("headless").and_then(Value::as_bool), Some(true));
}

#[test]
fn agent_config_omits_default_bools() {
    let config = AgentConfig::default();
    let json = serde_json::to_value(&config).unwrap();
    // false bools should be skipped
    assert!(json.get("headless").is_none());
    assert!(json.get("autoApprove").is_none());
    assert!(json.get("trustWorkspace").is_none());
}

#[test]
fn agent_config_omits_none_optionals() {
    let config = AgentConfig::default();
    let json = serde_json::to_value(&config).unwrap();
    assert!(json.get("agent").is_none());
    assert!(json.get("maxTurns").is_none());
    assert!(json.get("maxPrice").is_none());
    assert!(json.get("maxSessionTokens").is_none());
}

#[test]
fn agent_config_includes_budget_fields_when_set() {
    let config = AgentConfig {
        max_turns: Some(10),
        max_price: Some(2.5),
        max_session_tokens: Some(50000),
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json.get("maxTurns").and_then(Value::as_u64), Some(10));
    assert_eq!(json.get("maxPrice").and_then(Value::as_f64), Some(2.5));
    assert_eq!(
        json.get("maxSessionTokens").and_then(Value::as_u64),
        Some(50000)
    );
}

#[test]
fn agent_config_includes_agent_and_auto_approve() {
    let config = AgentConfig {
        agent: Some("ask".into()),
        auto_approve: true,
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json.get("agent").and_then(Value::as_str), Some("ask"));
    assert_eq!(json.get("autoApprove").and_then(Value::as_bool), Some(true));
}

#[test]
fn agent_config_includes_tool_filters() {
    let config = AgentConfig {
        enabled_tools: vec!["bash".into()],
        disabled_tools: vec!["web_search".into()],
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(
        json.get("enabledTools")
            .and_then(|v| v.as_array())
            .map(|a| a.len()),
        Some(1)
    );
    assert_eq!(
        json.get("disabledTools")
            .and_then(|v| v.as_array())
            .map(|a| a.len()),
        Some(1)
    );
}

#[test]
fn agent_config_includes_trust_and_workspace_roots() {
    let config = AgentConfig {
        trust_workspace: true,
        workspace_roots: vec!["/extra/dir".into()],
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(
        json.get("trustWorkspace").and_then(Value::as_bool),
        Some(true)
    );
    assert_eq!(
        json.get("workspaceRoots")
            .and_then(|v| v.as_array())
            .map(|a| a.len()),
        Some(1)
    );
}

#[test]
fn streaming_applies_completion_patch_before_emitting() {
    let mut output = Output::new(OutputFormat::Streaming);
    let added = serde_json::json!({
        "entry": {
            "id": "assistant-1",
            "generationStatus": "in_progress",
            "content": [{"type": "text", "text": "hello"}]
        }
    });
    assert!(output.consume_entry(&added).unwrap().is_none());

    let updated = serde_json::json!({
        "entryId": "assistant-1",
        "patch": [
            {"op": "append", "path": "/content/0/text", "value": " world"},
            {"op": "replace", "path": "/generationStatus", "value": "completed"}
        ]
    });
    let completed = output.consume_entry(&updated).unwrap().unwrap();

    assert_eq!(completed["generationStatus"], "completed");
    assert_eq!(completed["content"][0]["text"], "hello world");
}

#[test]
fn streaming_pending_entries_are_bounded() {
    let mut output = Output::new(OutputFormat::Streaming);
    for index in 0..512 {
        let params = serde_json::json!({
            "entry": {"id": format!("entry-{index}"), "generationStatus": "in_progress"}
        });
        assert!(output.consume_entry(&params).is_ok());
    }
    let overflow = serde_json::json!({
        "entry": {"id": "overflow", "generationStatus": "in_progress"}
    });
    assert!(output.consume_entry(&overflow).is_err());
}

#[test]
fn user_home_falls_back_to_windows_userprofile() {
    let user_profile = OsString::from(r"C:\Users\vibe");
    assert_eq!(
        user_home_from(None, Some(user_profile)),
        Some(PathBuf::from(r"C:\Users\vibe"))
    );
}

#[test]
fn last_assistant_text_joins_multiple_blocks() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "second"},
            ]
        }
    ]);
    let text = last_assistant_text(&history).unwrap();
    assert_eq!(text, "first\n\nsecond");
}

#[test]
fn last_assistant_text_skips_a_draft_a_hook_sent_back_for_a_retry() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Before."}],
            "outcome": {"type": "committed"}
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Draft."}],
            "outcome": {"type": "discarded", "cause": "retry"}
        }
    ]);
    assert_eq!(last_assistant_text(&history).as_deref(), Some("Before."));
}

#[test]
fn last_assistant_text_keeps_an_interrupted_partial_answer() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Partial."}],
            "outcome": {"type": "discarded", "cause": "interrupt"}
        }
    ]);
    assert_eq!(last_assistant_text(&history).as_deref(), Some("Partial."));
}

#[test]
fn last_assistant_text_skips_user_messages() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "text", "text": "hello"}]
        }
    ]);
    assert!(last_assistant_text(&history).is_none());
}

#[test]
fn last_assistant_text_finds_last_assistant() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "old"}]
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "new"}]
        }
    ]);
    assert_eq!(last_assistant_text(&history).unwrap(), "new");
}

#[test]
fn a_resumed_run_answers_only_with_its_own_turn() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "turnId": "earlier",
            "content": [{"type": "text", "text": "Looked it up."}]
        },
        {"type": "message", "role": "user", "turnId": "this-run", "content": []}
    ]);
    assert!(last_assistant_text(&turn_entries(&history, Some("this-run"))).is_none());
    assert!(turn_entries(&history, None).as_array().unwrap().is_empty());
    assert_eq!(
        last_assistant_text(&turn_entries(&history, Some("earlier"))).as_deref(),
        Some("Looked it up.")
    );
}

#[test]
fn last_assistant_text_empty_string_is_none() {
    let history = serde_json::json!([
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": ""}]
        }
    ]);
    assert!(last_assistant_text(&history).is_none());
}

#[test]
fn last_assistant_text_skips_non_message_entries() {
    let history = serde_json::json!([
        {"type": "effect", "generationStatus": "completed"}
    ]);
    assert!(last_assistant_text(&history).is_none());
}

#[test]
fn trust_warning_lists_detected_files() {
    let resp = serde_json::json!({
        "status": "untrusted",
        "details": {
            "cwd": "/work",
            "detectedFiles": [".vibe/settings.toml"],
            "repoDetectedFiles": []
        }
    });
    let warning = warning_from(&resp).unwrap();
    assert_eq!(
        warning,
        "Warning: /work is not trusted; project configuration (.vibe/settings.toml) will be ignored. Re-run with --trust to trust this folder temporarily."
    );
}

#[test]
fn trust_warning_dedupes_and_keeps_first_seen_order() {
    let resp = serde_json::json!({
        "status": "untrusted",
        "details": {
            "cwd": "/work",
            "detectedFiles": ["a.toml", "b.toml"],
            "repoDetectedFiles": ["b.toml", "c.toml"]
        }
    });
    let warning = warning_from(&resp).unwrap();
    assert!(warning.contains("(a.toml, b.toml, c.toml)"));
}

#[test]
fn trust_warning_none_when_trusted() {
    let resp = serde_json::json!({"status": "trusted", "details": null});
    assert!(warning_from(&resp).is_none());
}

#[test]
fn trust_warning_none_without_detected_files() {
    let resp = serde_json::json!({
        "status": "untrusted",
        "details": {"cwd": "/work", "detectedFiles": [], "repoDetectedFiles": []}
    });
    assert!(warning_from(&resp).is_none());
}

#[test]
fn turn_id_from_accepts_a_string_id() {
    let resp = serde_json::json!({"turn": {"id": "turn-42"}});
    assert_eq!(turn_id_from(&resp).unwrap(), "turn-42");
}

#[test]
fn turn_id_from_rejects_a_missing_id() {
    let resp = serde_json::json!({"turn": {}});
    let err = turn_id_from(&resp).unwrap_err();
    assert!(err.to_string().contains("missing turn id"));
}

#[test]
fn turn_id_from_rejects_a_non_string_id() {
    let resp = serde_json::json!({"turn": {"id": 42}});
    assert!(turn_id_from(&resp).is_err());
}

#[test]
fn piped_prompt_accepts_multibyte_under_the_cap() {
    let prompt = parse_piped_prompt("héllo wörld".as_bytes().to_vec());
    assert!(matches!(prompt, PipedPrompt::Prompt(ref t) if t == "héllo wörld"));
}

#[test]
fn piped_prompt_oversized_reports_the_cap_even_when_splitting_a_char() {
    let mut bytes = vec![b'a'; MAX_STDIN_PROMPT_BYTES];
    bytes.push("é".as_bytes()[0]);
    assert!(matches!(parse_piped_prompt(bytes), PipedPrompt::TooLarge));
}

#[test]
fn piped_prompt_at_exactly_the_cap_is_accepted() {
    let bytes = vec![b'a'; MAX_STDIN_PROMPT_BYTES];
    assert!(matches!(
        parse_piped_prompt(bytes),
        PipedPrompt::Prompt(ref t) if t.len() == MAX_STDIN_PROMPT_BYTES
    ));
}

#[test]
fn piped_prompt_rejects_a_split_multibyte_char_under_the_cap() {
    let bytes = "é".as_bytes()[..1].to_vec();
    assert!(matches!(
        parse_piped_prompt(bytes),
        PipedPrompt::InvalidUtf8
    ));
}

#[test]
fn piped_prompt_empty_or_whitespace_is_no_prompt() {
    assert!(matches!(parse_piped_prompt(vec![]), PipedPrompt::Empty));
    assert!(matches!(
        parse_piped_prompt("  \n\t ".as_bytes().to_vec()),
        PipedPrompt::Empty
    ));
}

fn headless_options_with_budgets() -> HeadlessOptions {
    HeadlessOptions {
        prompt: "hi".into(),
        session: HeadlessSession::New,
        output: OutputFormat::Text,
        max_turns: Some(5),
        max_price: Some(1.5),
        max_tokens: Some(10000),
        time_limit: None,
        output_dir: None,
        system_prompt: None,
        model: None,
        agent: None,
        auto_approve: false,
        enabled_tools: vec![],
        disabled_tools: vec![],
        trust: false,
        add_dir: vec![],
        worktree: None,
        agent_socket: None,
    }
}

#[test]
fn agent_config_serializes_max_turns_when_set() {
    let config = agent_config(&headless_options_with_budgets(), None, None).unwrap();
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json.get("maxTurns").and_then(Value::as_u64), Some(5));
    // Price and token budgets should be omitted (None + skip_serializing_if).
    assert!(json.get("maxPrice").is_none());
    assert!(json.get("maxSessionTokens").is_none());
}

#[test]
fn agent_config_drops_the_terminal_key_for_an_agent_socket_run() {
    let mut options = headless_options_with_budgets();
    let key = || Some("/dev/ttys003".to_owned());
    let in_terminal = agent_config(&options, None, key()).unwrap();
    assert_eq!(in_terminal.terminal_key, key());
    options.agent_socket = Some("agent.sock".into());
    let in_sandbox = agent_config(&options, None, key()).unwrap();
    assert_eq!(in_sandbox.terminal_key, None);
}

#[test]
fn turn_error_outcome_maps_wire_codes() {
    use vibe_rs::headless::turn_error_outcome;
    use vibe_rs::run_export::RunOutcome;

    // Codes come over the wire in lower case (the app server's TurnErrorCode StrEnum).
    assert_eq!(
        turn_error_outcome(Some("invalid_api_key")),
        RunOutcome::ConfigError
    );
    assert_eq!(
        turn_error_outcome(Some("invalid_model")),
        RunOutcome::ConfigError
    );
    assert_eq!(
        turn_error_outcome(Some("context_too_long")),
        RunOutcome::Length
    );
    assert_eq!(
        turn_error_outcome(Some("response_too_long")),
        RunOutcome::Length
    );
    // The model's own answer, so the run is scored rather than dropped.
    assert_eq!(turn_error_outcome(Some("refusal")), RunOutcome::Refusal);
    assert_eq!(
        turn_error_outcome(Some("backend_error")),
        RunOutcome::InfrastructureFailure
    );
    assert_eq!(turn_error_outcome(None), RunOutcome::InfrastructureFailure);
    // An upper-case code never matched: a bad API key is a config error, not
    // an infrastructure failure a runner would retry.
    assert_eq!(
        turn_error_outcome(Some("INVALID_API_KEY")),
        RunOutcome::InfrastructureFailure
    );
}

#[test]
fn stopped_report_maps_the_requested_outcome() {
    use vibe_rs::headless::stopped_report;
    use vibe_rs::run_export::{RunOutcome, TurnStop};

    let (outcome, stop_reason, error, _) = stopped_report(RunOutcome::TurnLimit, true, None);
    assert_eq!(outcome, RunOutcome::TurnLimit);
    assert_eq!(stop_reason, Some(TurnStop::Limit));
    assert!(error.is_none());

    let (outcome, stop_reason, _, _) = stopped_report(RunOutcome::Terminated, true, None);
    assert_eq!(outcome, RunOutcome::Terminated);
    assert_eq!(stop_reason, Some(TurnStop::Interrupted));

    let (_, stop_reason, _, _) = stopped_report(RunOutcome::Deadline, true, None);
    assert_eq!(stop_reason, Some(TurnStop::Interrupted));

    // A stop before the turn started has no stop reason to report.
    let (outcome, stop_reason, _, _) = stopped_report(RunOutcome::Terminated, false, None);
    assert_eq!(outcome, RunOutcome::Terminated);
    assert_eq!(stop_reason, None);
}

#[test]
fn final_outcome_lets_an_enforced_limit_win_over_a_finished_turn() {
    use std::sync::Arc;

    use vibe_rs::headless::final_outcome;
    use vibe_rs::headless_run::StopRequests;
    use vibe_rs::run_export::{RunError, RunLimits, RunOutcome};

    // The turn completed on its own, but the run went past --max-turns: the
    // enforced limit wins.
    let stop = Arc::new(StopRequests::new(RunLimits {
        max_turns: Some(1),
        ..Default::default()
    }));
    stop.request(RunOutcome::TurnLimit);
    assert_eq!(
        final_outcome(RunOutcome::Finished, &None, &stop),
        RunOutcome::TurnLimit
    );

    // An externally requested stop (deadline, signal) loses to a finished
    // turn: the run did finish.
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    stop.request(RunOutcome::Deadline);
    assert_eq!(
        final_outcome(RunOutcome::Finished, &None, &stop),
        RunOutcome::Finished
    );

    // An error still wins, as it says more.
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    stop.request(RunOutcome::PriceLimit);
    let error = Some(RunError {
        message: "boom".into(),
        code: None,
    });
    assert_eq!(
        final_outcome(RunOutcome::Finished, &error, &stop),
        RunOutcome::Finished
    );

    // No request: the turn's own outcome stands.
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    assert_eq!(
        final_outcome(RunOutcome::Length, &None, &stop),
        RunOutcome::Length
    );
}
