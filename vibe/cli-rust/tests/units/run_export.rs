//! Tests for run_export types: exit codes, turn stop parsing, serialization.

use serde_json::{json, Value};
use vibe_rs::run_export::{
    turn_stop_from_turn, RunError, RunExport, RunLimits, RunOutcome, RunResult, RunUsage,
    RunWarning, TurnStop,
};

#[test]
fn outcome_exit_codes() {
    assert_eq!(RunOutcome::Finished.exit_code(), 0);
    assert_eq!(RunOutcome::UsageError.exit_code(), 1);
    assert_eq!(RunOutcome::ConfigError.exit_code(), 1);
    assert_eq!(RunOutcome::InfrastructureFailure.exit_code(), 2);
    assert_eq!(RunOutcome::TurnLimit.exit_code(), 3);
    assert_eq!(RunOutcome::TokenLimit.exit_code(), 3);
    assert_eq!(RunOutcome::PriceLimit.exit_code(), 3);
    assert_eq!(RunOutcome::Deadline.exit_code(), 3);
    assert_eq!(RunOutcome::Terminated.exit_code(), 3);
    assert_eq!(RunOutcome::Length.exit_code(), 3);
}

#[test]
fn outcome_display() {
    assert_eq!(RunOutcome::Finished.to_string(), "finished");
    assert_eq!(RunOutcome::UsageError.to_string(), "usage_error");
    assert_eq!(RunOutcome::ConfigError.to_string(), "config_error");
    assert_eq!(
        RunOutcome::InfrastructureFailure.to_string(),
        "infrastructure_failure"
    );
    assert_eq!(RunOutcome::TurnLimit.to_string(), "turn_limit");
    assert_eq!(RunOutcome::TokenLimit.to_string(), "token_limit");
    assert_eq!(RunOutcome::PriceLimit.to_string(), "price_limit");
    assert_eq!(RunOutcome::Deadline.to_string(), "deadline");
    assert_eq!(RunOutcome::Terminated.to_string(), "terminated");
    assert_eq!(RunOutcome::Length.to_string(), "length");
}

#[test]
fn turn_stop_from_turn_interrupted() {
    let turn = json!({"status": "interrupted", "stopReason": null});
    assert_eq!(turn_stop_from_turn(&turn), Some(TurnStop::Interrupted));
}

#[test]
fn turn_stop_from_turn_limit() {
    let turn = json!({"status": "completed", "stopReason": "limit"});
    assert_eq!(turn_stop_from_turn(&turn), Some(TurnStop::Limit));
}

#[test]
fn turn_stop_from_turn_length() {
    let turn = json!({"status": "completed", "stopReason": "length"});
    assert_eq!(turn_stop_from_turn(&turn), Some(TurnStop::Length));
}

#[test]
fn turn_stop_from_turn_none() {
    let turn = json!({"status": "completed", "stopReason": null});
    assert_eq!(turn_stop_from_turn(&turn), None);
}

#[test]
fn turn_stop_from_turn_no_stop_reason() {
    let turn = json!({"status": "completed"});
    assert_eq!(turn_stop_from_turn(&turn), None);
}

#[test]
fn turn_stop_from_turn_interrupted_wins_over_reason() {
    // Interrupted status wins even if stopReason is set.
    let turn = json!({"status": "interrupted", "stopReason": "limit"});
    assert_eq!(turn_stop_from_turn(&turn), Some(TurnStop::Interrupted));
}

#[test]
fn run_result_serialization_matches_export_contract() {
    let result = RunResult {
        outcome: RunOutcome::Finished,
        stop_reason: None,
        error: None,
        session_id: Some("abc123".into()),
        usage: Some(RunUsage {
            input_tokens: 100,
            output_tokens: 50,
            cached_input_tokens: 20,
            total_tokens: 150,
        }),
        cost_usd: Some(0.001),
        steps: Some(3),
        config: None,
        warnings: None,
    };
    let json = serde_json::to_value(&result).unwrap();
    // Field names and nulls follow the export contract, which consumers parse.
    assert_eq!(json["outcome"], "finished");
    assert_eq!(json["stop_reason"], Value::Null);
    assert_eq!(json["error"], Value::Null);
    assert_eq!(json["session_id"], "abc123");
    assert_eq!(json["usage"]["input_tokens"], 100);
    assert_eq!(json["usage"]["output_tokens"], 50);
    assert_eq!(json["usage"]["cached_input_tokens"], 20);
    assert_eq!(json["usage"]["total_tokens"], 150);
    assert_eq!(json["cost_usd"], 0.001);
    assert_eq!(json["steps"], 3);
    assert_eq!(json["config"], Value::Null);
    assert!(json.get("warnings").is_none());
}

#[test]
fn run_export_serialization_matches_export_contract() {
    let export = RunExport {
        result: RunResult {
            outcome: RunOutcome::TurnLimit,
            stop_reason: Some(TurnStop::Limit),
            error: Some(RunError {
                message: "limit reached".into(),
                code: None,
            }),
            session_id: Some("s1".into()),
            usage: None,
            cost_usd: None,
            steps: None,
            config: None,
            warnings: Some(vec![RunWarning {
                message: "a config file did not parse".into(),
                source: Some("/tmp/agent.toml".into()),
            }]),
        },
        schema_version: 1,
        vibe_version: "2.25.8".into(),
        journal_dir: None,
        started_at: "2026-10-05T10:00:00.000000Z".into(),
        ended_at: "2026-10-05T10:01:00.000000Z".into(),
        limits: RunLimits {
            max_turns: Some(5),
            max_price: None,
            max_tokens: None,
            time_limit_s: None,
        },
        exit_code: 3,
    };
    let json = serde_json::to_value(&export).unwrap();
    assert_eq!(json["outcome"], "turn_limit");
    assert_eq!(json["stop_reason"], "limit");
    assert_eq!(json["error"]["message"], "limit reached");
    assert_eq!(json["schema_version"], 1);
    assert_eq!(json["vibe_version"], "2.25.8");
    assert_eq!(json["exit_code"], 3);
    assert_eq!(json["journal_dir"], Value::Null);
    assert_eq!(json["limits"]["max_turns"], 5);
    assert_eq!(json["limits"]["max_price"], Value::Null);
    assert_eq!(
        json["warnings"][0]["message"],
        "a config file did not parse"
    );
    assert_eq!(json["warnings"][0]["source"], "/tmp/agent.toml");
}

#[test]
fn run_limits_default() {
    let limits = RunLimits::default();
    assert!(limits.max_turns.is_none());
    assert!(limits.max_price.is_none());
    assert!(limits.max_tokens.is_none());
    assert!(limits.time_limit_s.is_none());
}

#[test]
fn run_error_serialization() {
    let error = RunError {
        message: "something went wrong".into(),
        code: Some("INVALID_API_KEY".into()),
    };
    let json = serde_json::to_value(&error).unwrap();
    assert_eq!(json["message"], "something went wrong");
    assert_eq!(json["code"], "INVALID_API_KEY");
}

#[test]
fn turn_stop_display() {
    assert_eq!(TurnStop::Interrupted.to_string(), "interrupted");
    assert_eq!(TurnStop::Limit.to_string(), "limit");
    assert_eq!(TurnStop::Length.to_string(), "length");
}

#[test]
fn outcome_refusal_exit_code_and_display() {
    // A refusal is the model's own answer, scored, exit 3.
    assert_eq!(RunOutcome::Refusal.exit_code(), 3);
    assert_eq!(RunOutcome::Refusal.to_string(), "refusal");
    let json = serde_json::to_value(RunOutcome::Refusal).unwrap();
    assert_eq!(json, "refusal");
}

#[test]
fn redact_config_hides_secret_keys() {
    use vibe_rs::run_export::redact_config;

    let config = json!({
        "model": "mistral-large",
        "headers": {"Authorization": "Bearer secret"},
        "extra_headers": {"X-Key": "secret"},
        "env": {"TOKEN": "secret"},
        "args": ["--api-key", "secret"],
    });
    let redacted = redact_config(config);
    // The shape is kept; the credential values are replaced whole.
    assert_eq!(redacted["model"], "mistral-large");
    assert_eq!(redacted["headers"], json!({"Authorization": "<redacted>"}));
    assert_eq!(redacted["extra_headers"], json!({"X-Key": "<redacted>"}));
    assert_eq!(redacted["env"], json!({"TOKEN": "<redacted>"}));
    assert_eq!(redacted["args"], json!(["<redacted>", "<redacted>"]));
}

#[test]
fn redact_config_redacts_mcp_server_command() {
    use vibe_rs::run_export::redact_config;

    let config = json!({
        "mcp_servers": [
            {"command": "npx -y @mcp/server --token secret", "env": {"KEY": "secret"}},
            {"command": "server-only"},
            {"url": "https://user:pass@mcp.example.com/sse?token=secret"},
        ],
    });
    let redacted = redact_config(config);
    let servers = redacted["mcp_servers"].as_array().unwrap();
    // The program says which server runs; what follows it may be a credential.
    assert_eq!(servers[0]["command"], "npx <redacted>");
    assert_eq!(servers[0]["env"], json!({"KEY": "<redacted>"}));
    assert_eq!(servers[1]["command"], "server-only");
    // A URL keeps its host and path but loses its user info and query.
    assert_eq!(
        servers[2]["url"],
        "https://<redacted>@mcp.example.com/sse?<redacted>"
    );
}

#[test]
fn redact_config_keeps_non_secret_urls_and_values() {
    use vibe_rs::run_export::redact_config;

    let config = json!({
        "api_base": "https://api.example.com/v1",
        "docs": "https://example.com/docs?a=1#frag",
        "endpoint": "not a url",
        "count": 3,
        "enabled": true,
        "nested": {"list": ["https://h.example.com/p", 1, null]},
    });
    let redacted = redact_config(config);
    assert_eq!(redacted["api_base"], "https://api.example.com/v1");
    assert_eq!(redacted["docs"], "https://example.com/docs?<redacted>#frag");
    assert_eq!(redacted["endpoint"], "not a url");
    assert_eq!(redacted["count"], 3);
    assert_eq!(redacted["enabled"], true);
    assert_eq!(
        redacted["nested"]["list"],
        json!(["https://h.example.com/p", 1, null])
    );
}

#[test]
fn copy_journal_redacts_metadata_config() {
    use std::fs;
    use vibe_rs::run_export::{copy_journal, JOURNAL_DIRNAME, METADATA_FILENAME};

    let root = tempfile::tempdir().unwrap();
    let session_dir = root.path().join("session-src");
    fs::create_dir_all(&session_dir).unwrap();
    fs::write(
        session_dir.join(METADATA_FILENAME),
        serde_json::to_string_pretty(&json!({
            "session_id": "s1",
            "config": {
                "mcp_servers": [{"command": "npx -y server --token secret"}],
                "headers": {"Authorization": "Bearer secret"},
                "model": "mistral-large",
            },
        }))
        .unwrap(),
    )
    .unwrap();
    fs::write(session_dir.join("journal.jsonl"), "{}\n").unwrap();
    // A snapshot that cannot be parsed cannot be redacted either: it is dropped.
    let other = root.path().join("session-bad");
    fs::create_dir_all(&other).unwrap();
    fs::write(other.join(METADATA_FILENAME), "not json").unwrap();

    let output_dir = root.path().join("out");
    let name = copy_journal(&session_dir, &output_dir).unwrap();
    assert_eq!(name, JOURNAL_DIRNAME);
    let meta: Value = serde_json::from_str(
        &fs::read_to_string(output_dir.join(JOURNAL_DIRNAME).join(METADATA_FILENAME)).unwrap(),
    )
    .unwrap();
    // The rest of the metadata is kept; only the config is redacted.
    assert_eq!(meta["session_id"], "s1");
    assert_eq!(meta["config"]["model"], "mistral-large");
    assert_eq!(
        meta["config"]["mcp_servers"][0]["command"],
        "npx <redacted>"
    );
    assert_eq!(
        meta["config"]["headers"],
        json!({"Authorization": "<redacted>"})
    );
    assert!(
        fs::read_to_string(output_dir.join(JOURNAL_DIRNAME).join("journal.jsonl"))
            .unwrap()
            .contains("{}")
    );

    let output_dir2 = root.path().join("out-bad");
    copy_journal(&other, &output_dir2).unwrap();
    assert!(!output_dir2
        .join(JOURNAL_DIRNAME)
        .join(METADATA_FILENAME)
        .exists());
}

#[test]
fn copy_journal_skips_partial_write_files() {
    use std::fs;
    use vibe_rs::run_export::{copy_journal, JOURNAL_DIRNAME};

    // A session's `*.json.tmp` and
    // `*.jsonl.tmp` hold unredacted config and never enter the export.
    let root = tempfile::tempdir().unwrap();
    let session_dir = root.path().join("session-src");
    fs::create_dir_all(&session_dir).unwrap();
    fs::write(session_dir.join("journal.jsonl"), "{}\n").unwrap();
    fs::write(
        session_dir.join("journal.jsonl.tmp"),
        r#"{"config": {"headers": {"Authorization": "Bearer secret"}}}"#,
    )
    .unwrap();
    fs::write(session_dir.join("meta.json.tmp"), r#"{"config": {}}"#).unwrap();

    let output_dir = root.path().join("out");
    copy_journal(&session_dir, &output_dir).unwrap();
    let mut copied: Vec<_> = fs::read_dir(output_dir.join(JOURNAL_DIRNAME))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect();
    copied.sort();
    assert_eq!(copied, vec![std::ffi::OsString::from("journal.jsonl")]);
}

#[test]
fn copy_journal_removes_staging_dir_on_error() {
    use std::fs;
    use vibe_rs::run_export::{copy_journal, JOURNAL_DIRNAME};

    // A failed copy leaves no staging directory behind: it may hold an
    // unredacted config snapshot.
    let root = tempfile::tempdir().unwrap();
    let not_a_dir = root.path().join("not-a-dir");
    fs::write(&not_a_dir, "x").unwrap();
    let output_dir = root.path().join("out");
    fs::create_dir_all(&output_dir).unwrap();

    assert!(copy_journal(&not_a_dir, &output_dir).is_err());
    assert!(!output_dir.join(".session.tmp").exists());
    assert!(!output_dir.join(JOURNAL_DIRNAME).exists());
}

#[test]
fn copy_journal_keeps_the_previous_journal_when_the_copy_fails() {
    use std::fs;
    use vibe_rs::run_export::{copy_journal, JOURNAL_DIRNAME};

    // The previous journal is set aside and restored if
    // the swap fails, so a failed copy never destroys the previous journal.
    let root = tempfile::tempdir().unwrap();
    let session_dir = root.path().join("session-src");
    fs::create_dir_all(&session_dir).unwrap();
    fs::write(session_dir.join("journal.jsonl"), "first\n").unwrap();
    let not_a_dir = root.path().join("not-a-dir");
    fs::write(&not_a_dir, "x").unwrap();

    let output_dir = root.path().join("out");
    copy_journal(&session_dir, &output_dir).unwrap();
    assert!(copy_journal(&not_a_dir, &output_dir).is_err());

    // The first journal is still in place, and nothing is left aside.
    assert_eq!(
        fs::read_to_string(output_dir.join(JOURNAL_DIRNAME).join("journal.jsonl")).unwrap(),
        "first\n"
    );
    assert!(!output_dir.join(".session.tmp").exists());
    assert!(!output_dir.join(".session.old").exists());
}

#[test]
fn copy_journal_settles_a_journal_left_aside() {
    use std::fs;
    use vibe_rs::run_export::{copy_journal, JOURNAL_DIRNAME};

    // A crashed copy's set-aside
    // journal is settled before the next copy runs.
    let root = tempfile::tempdir().unwrap();
    let session_dir = root.path().join("session-src");
    fs::create_dir_all(&session_dir).unwrap();
    fs::write(session_dir.join("journal.jsonl"), "new\n").unwrap();

    // With a journal in place, the set-aside one is older and goes.
    let output_dir = root.path().join("out-with-target");
    fs::create_dir_all(output_dir.join(JOURNAL_DIRNAME)).unwrap();
    fs::write(
        output_dir.join(JOURNAL_DIRNAME).join("journal.jsonl"),
        "current\n",
    )
    .unwrap();
    fs::create_dir_all(output_dir.join(".session.old")).unwrap();
    fs::write(
        output_dir.join(".session.old").join("journal.jsonl"),
        "older\n",
    )
    .unwrap();
    copy_journal(&session_dir, &output_dir).unwrap();
    assert_eq!(
        fs::read_to_string(output_dir.join(JOURNAL_DIRNAME).join("journal.jsonl")).unwrap(),
        "new\n"
    );
    assert!(!output_dir.join(".session.old").exists());

    // Without one, the copy crashed between setting it aside and renaming
    // its own copy in, so the set-aside one is the latest journal and the
    // new copy replaces it.
    let output_dir = root.path().join("out-without-target");
    fs::create_dir_all(output_dir.join(".session.old")).unwrap();
    fs::write(
        output_dir.join(".session.old").join("journal.jsonl"),
        "latest\n",
    )
    .unwrap();
    copy_journal(&session_dir, &output_dir).unwrap();
    assert_eq!(
        fs::read_to_string(output_dir.join(JOURNAL_DIRNAME).join("journal.jsonl")).unwrap(),
        "new\n"
    );
    assert!(!output_dir.join(".session.old").exists());
}

#[test]
fn write_run_export_replaces_an_existing_export() {
    use std::fs;
    use vibe_rs::run_export::{write_run_export, EXPORT_FILENAME};

    // Writing the export replaces the destination: a second run with the
    // same --output-dir must not leave the first run's export in place.
    let export = || RunExport {
        result: RunResult {
            outcome: RunOutcome::Finished,
            stop_reason: None,
            error: None,
            session_id: None,
            usage: None,
            cost_usd: None,
            steps: None,
            config: None,
            warnings: None,
        },
        schema_version: 1,
        vibe_version: "2.26.0".into(),
        journal_dir: None,
        started_at: "2026-10-08T10:00:00.000000Z".into(),
        ended_at: "2026-10-08T10:01:00.000000Z".into(),
        limits: RunLimits::default(),
        exit_code: 0,
    };

    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    write_run_export(&output_dir, &export()).unwrap();
    let first = fs::read_to_string(output_dir.join(EXPORT_FILENAME)).unwrap();

    let mut second = export();
    second.ended_at = "2026-10-08T10:02:00.000000Z".into();
    write_run_export(&output_dir, &second).unwrap();
    let replaced = fs::read_to_string(output_dir.join(EXPORT_FILENAME)).unwrap();

    assert_ne!(first, replaced);
    assert!(replaced.contains("10:02:00"));
    assert!(!output_dir.join(".export.json.tmp").exists());
}
