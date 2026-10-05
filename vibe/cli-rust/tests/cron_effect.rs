//! CRON presentation uses generic effect headers and plain-string result bodies.

use serde_json::{json, Value};
use vibe_rs::server::EffectEntry;

fn fixture(name: &str) -> Value {
    let fixtures: Value = serde_json::from_str(include_str!(
        "../../../client-e2e/fixtures/cron_effects.json"
    ))
    .unwrap();
    fixtures[name].clone()
}

fn effect(name: &str) -> EffectEntry {
    serde_json::from_value(fixture(name)).unwrap()
}

fn body(effect: &EffectEntry) -> Vec<String> {
    effect.body().into_iter().map(|line| line.text).collect()
}

#[test]
fn running_cron_calls_keep_generic_identity_and_human_verbs() {
    for (name, verb, message) in [
        (
            "interval",
            "Scheduling",
            "every 1 minute 30 seconds: check CI",
        ),
        (
            "calendar",
            "Scheduling",
            "cron 0 9 * * 1-5 (local time): review weekday builds",
        ),
        ("empty", "Listing", "scheduled prompts"),
        ("mixed", "Listing", "scheduled prompts"),
        ("cancelled", "Cancelling", "scheduled prompt abc"),
        ("cleared", "Clearing", "scheduled prompts"),
    ] {
        let mut value = fixture(name);
        value["state"] = json!({"status": "running", "outputText": ""});
        let entry: EffectEntry = serde_json::from_value(value).unwrap();

        assert_eq!(entry.kind(), Some("tool"));
        assert_eq!(
            entry.detail.as_ref().unwrap().tool_name.as_deref(),
            Some("vibe.cron")
        );
        assert_eq!(entry.summary(), (verb, message, ""));
        assert_eq!(entry.status_text(), format!("{verb} recurring prompts"));
        assert!(!entry.is_terminal());
        assert!(!entry.has_body());
        assert!(entry.body().is_empty());
    }
}

#[test]
fn completed_cron_calls_use_settled_headers_and_expandable_string_output() {
    for (name, verb, message) in [
        (
            "interval",
            "Scheduled",
            "every 1 minute 30 seconds: check CI",
        ),
        (
            "calendar",
            "Scheduled",
            "cron 0 9 * * 1-5 (local time): review weekday builds",
        ),
        ("empty", "Listed", "0 scheduled prompts"),
        ("mixed", "Listed", "2 scheduled prompts"),
        ("cancelled", "Cancelled", "check CI"),
        ("cleared", "Cleared", "2 scheduled loops"),
    ] {
        let entry = effect(name);
        assert_eq!(entry.summary(), (verb, message, ""));
        assert!(entry.is_terminal());
        assert!(entry.success());
        assert!(!entry.is_muted());
        assert!(entry.is_collapsible() && entry.has_body());
        assert!(fixture(name)["state"]["output"].is_string());
        assert_eq!(body(&entry).join("\n"), entry.output_text());
    }
}

#[test]
fn interval_result_shows_prompt_schedule_next_run_and_id_without_json() {
    assert_eq!(
        body(&effect("interval")),
        [
            "Prompt: check CI",
            "Schedule: every 1 minute 30 seconds",
            "Next run: 2026-09-22 09:00:00 UTC",
            "ID: abc",
        ]
    );
}

#[test]
fn mixed_list_preserves_blank_separator_and_local_calendar_schedule() {
    assert_eq!(
        body(&effect("mixed")),
        [
            "Prompt: check CI",
            "Schedule: every 1 minute 30 seconds",
            "Next run: 2026-09-22 09:00:00 UTC",
            "ID: abc",
            "",
            "Prompt: review weekday builds",
            "Schedule: cron 0 9 * * 1-5 (local time)",
            "Next run: 2026-09-23 09:00:00 UTC",
            "ID: weekdays",
        ]
    );
}

#[test]
fn empty_list_and_clear_render_simple_sentences() {
    assert_eq!(body(&effect("empty")), ["No scheduled prompts."]);
    assert_eq!(body(&effect("cleared")), ["Cleared 2 scheduled loops"]);
}

#[test]
fn cancelled_schedule_is_completed_and_omits_next_run() {
    let entry = effect("cancelled");
    assert_eq!(entry.status(), Some("completed"));
    assert_eq!(
        body(&entry),
        [
            "Prompt: check CI",
            "Schedule: every 1 minute 30 seconds",
            "ID: abc",
        ]
    );
}

#[test]
fn failed_cron_call_uses_settled_detail_and_error_body() {
    let entry = effect("failed");
    assert_eq!(
        entry.summary(),
        ("Cancelled", "scheduled prompt missing", "")
    );
    assert!(!entry.success());
    assert!(entry.is_terminal() && entry.has_body());
    assert_eq!(
        body(&entry),
        ["Error: No scheduled loop found with ID missing"]
    );
}
