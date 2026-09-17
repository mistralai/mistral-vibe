//! Tests for headless-mode logic: disabled tools, AgentConfig wiring, output formatting.
use std::ffi::OsString;
use std::path::PathBuf;

use serde_json::Value;
use vibe_rs::cli::OutputFormat;
use vibe_rs::headless::{build_disabled_tools, turn_id_from};
use vibe_rs::headless_output::{last_assistant_text, Output};
use vibe_rs::headless_prompt::{parse_piped_prompt, PipedPrompt, MAX_STDIN_PROMPT_BYTES};
use vibe_rs::headless_trust::warning_from;
use vibe_rs::server::AgentConfig;
use vibe_rs::utils::paths::user_home_from;

#[test]
fn disabled_tools_appends_ask_user_question_and_exit_plan_mode() {
    let tools = build_disabled_tools(&[]);
    assert!(tools.contains(&"ask_user_question".into()));
    assert!(tools.contains(&"exit_plan_mode".into()));
}

#[test]
fn disabled_tools_preserves_explicit_and_dedupes() {
    let tools = build_disabled_tools(&["bash".into(), "ask_user_question".into()]);
    assert_eq!(tools.len(), 3); // bash + ask_user_question + exit_plan_mode
    assert!(tools.contains(&"bash".into()));
    assert!(tools.contains(&"exit_plan_mode".into()));
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
        agent: Some("plan".into()),
        auto_approve: true,
        ..Default::default()
    };
    let json = serde_json::to_value(&config).unwrap();
    assert_eq!(json.get("agent").and_then(Value::as_str), Some("plan"));
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
