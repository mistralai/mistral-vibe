//! Tests for headless run lifecycle: StopRequests, iso_now.

use std::sync::Arc;
use std::time::Duration;

use vibe_rs::headless_run::{iso_now, StopRequests};
use vibe_rs::run_export::{RunLimits, RunOutcome};

#[test]
fn request_first_wins() {
    let stop = StopRequests::new(RunLimits::default());
    stop.request(RunOutcome::Terminated);
    stop.request(RunOutcome::Deadline);
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::Terminated));
}

#[test]
fn requested_outcome_none_before_request() {
    let stop = StopRequests::new(RunLimits::default());
    assert_eq!(stop.requested_outcome(), None);
}

#[test]
fn observe_usage_under_limits_does_not_stop() {
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: Some(1.0),
        max_tokens: Some(1000),
        time_limit_s: None,
    });
    stop.observe_usage(500, 0.5);
    assert_eq!(stop.requested_outcome(), None);
}

#[test]
fn observe_usage_token_limit_triggers() {
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: None,
        max_tokens: Some(1000),
        time_limit_s: None,
    });
    stop.observe_usage(1001, 0.0);
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::TokenLimit));
}

#[test]
fn observe_usage_price_limit_triggers() {
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: Some(1.0),
        max_tokens: None,
        time_limit_s: None,
    });
    stop.observe_usage(0, 1.01);
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::PriceLimit));
}

#[test]
fn observe_usage_token_checked_before_price() {
    // When both limits are exceeded, token limit wins (checked first).
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: Some(0.5),
        max_tokens: Some(100),
        time_limit_s: None,
    });
    stop.observe_usage(101, 0.6);
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::TokenLimit));
}

#[test]
fn observe_usage_exact_limit_does_not_trigger() {
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: Some(1.0),
        max_tokens: Some(1000),
        time_limit_s: None,
    });
    // Exactly at the limit should not trigger (uses >).
    stop.observe_usage(1000, 1.0);
    assert_eq!(stop.requested_outcome(), None);
}

#[test]
fn observe_usage_no_limits_does_not_stop() {
    let stop = StopRequests::new(RunLimits::default());
    stop.observe_usage(u64::MAX, f64::MAX);
    assert_eq!(stop.requested_outcome(), None);
}

#[test]
fn limits_accessor_returns_configured_limits() {
    let limits = RunLimits {
        max_turns: Some(5),
        max_price: Some(2.0),
        max_tokens: Some(5000),
        time_limit_s: Some(60.0),
    };
    let stop = StopRequests::new(limits.clone());
    let got = stop.limits();
    assert_eq!(got.max_turns, Some(5));
    assert_eq!(got.max_price, Some(2.0));
    assert_eq!(got.max_tokens, Some(5000));
    assert_eq!(got.time_limit_s, Some(60.0));
}

#[tokio::test]
async fn wait_returns_immediately_when_already_requested() {
    let stop = StopRequests::new(RunLimits::default());
    stop.request(RunOutcome::Terminated);
    let outcome = stop.wait().await;
    assert_eq!(outcome, RunOutcome::Terminated);
}

#[tokio::test]
async fn wait_returns_deadline_after_time_limit() {
    let stop = StopRequests::new(RunLimits {
        max_turns: None,
        max_price: None,
        max_tokens: None,
        time_limit_s: Some(0.1),
    });
    let outcome = stop.wait().await;
    assert_eq!(outcome, RunOutcome::Deadline);
}

#[tokio::test]
async fn wait_returns_when_request_arrives() {
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    let stop_clone = Arc::clone(&stop);
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(50)).await;
        stop_clone.request(RunOutcome::PriceLimit);
    });
    let outcome = stop.wait().await;
    assert_eq!(outcome, RunOutcome::PriceLimit);
}

#[test]
fn iso_now_format() {
    let ts = iso_now();
    // ISO 8601 UTC: YYYY-MM-DDTHH:MM:SS.ffffffZ
    assert!(ts.len() >= 27, "timestamp too short: {ts}");
    let bytes = ts.as_bytes();
    assert_eq!(bytes[4], b'-', "expected dash at position 4: {ts}");
    assert_eq!(bytes[7], b'-', "expected dash at position 7: {ts}");
    assert_eq!(bytes[10], b'T', "expected T at position 10: {ts}");
    assert_eq!(bytes[13], b':', "expected colon at position 13: {ts}");
    assert_eq!(bytes[16], b':', "expected colon at position 16: {ts}");
    assert!(ts.ends_with('Z'), "expected trailing Z: {ts}");
    // Microsecond precision, as the export contract's timestamps carry: exactly
    // six fractional digits between the seconds and the Z.
    let fraction = &ts[20..ts.len() - 1];
    assert_eq!(fraction.len(), 6, "expected 6 fractional digits: {ts}");
    assert!(
        fraction.bytes().all(|b| b.is_ascii_digit()),
        "expected digits: {ts}"
    );
    // Year should start with 20 (2000s).
    assert!(ts.starts_with("20"), "expected year starting with 20: {ts}");
}

#[test]
fn iso_now_is_valid_date() {
    let ts = iso_now();
    // Parse the date portion to sanity-check.
    let date_part = &ts[..10];
    let parts: Vec<&str> = date_part.split('-').collect();
    assert_eq!(parts.len(), 3, "expected YYYY-MM-DD: {ts}");
    let year: u32 = parts[0].parse().expect("year should be numeric");
    let month: u32 = parts[1].parse().expect("month should be numeric");
    let day: u32 = parts[2].parse().expect("day should be numeric");
    assert!((2025..=2030).contains(&year), "unexpected year: {year}");
    assert!((1..=12).contains(&month), "unexpected month: {month}");
    assert!((1..=31).contains(&day), "unexpected day: {day}");
}

#[test]
fn invalid_time_limit_accepts_positive_finite() {
    use vibe_rs::headless_run::invalid_time_limit;

    assert_eq!(invalid_time_limit(None), None);
    assert_eq!(invalid_time_limit(Some(30.0)), None);
    assert_eq!(invalid_time_limit(Some(0.5)), None);
}

#[test]
fn invalid_time_limit_rejects_non_positive_and_non_finite() {
    use vibe_rs::headless_run::invalid_time_limit;

    // Rejected up front: they would panic in Duration::from_secs_f64 with
    // no export.
    assert!(invalid_time_limit(Some(0.0)).is_some());
    assert!(invalid_time_limit(Some(-5.0)).is_some());
    assert!(invalid_time_limit(Some(f64::NAN)).is_some());
    assert!(invalid_time_limit(Some(f64::INFINITY)).is_some());
    assert!(invalid_time_limit(Some(f64::NEG_INFINITY)).is_some());
}

#[test]
fn fail_writes_the_export_for_a_usage_error() {
    use clap::Parser;
    use serde_json::Value;
    use vibe_rs::cli::Cli;
    use vibe_rs::headless_run::HeadlessRun;

    // A usage error found before the run starts
    // still leaves an export when --output-dir is set.
    let root = tempfile::tempdir().unwrap();
    let output_dir = root.path().join("out");
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--output-dir",
        output_dir.to_str().unwrap(),
    ]);
    let run = HeadlessRun::for_cli(&cli).unwrap();
    let code = run.fail(
        RunOutcome::UsageError,
        "--workdir does not exist or is not a directory: /nope",
    );
    assert_eq!(code, 1);

    let export: Value =
        serde_json::from_str(&std::fs::read_to_string(output_dir.join("export.json")).unwrap())
            .unwrap();
    assert_eq!(export["outcome"], "usage_error");
    assert_eq!(export["exit_code"], 1);
    assert_eq!(
        export["error"]["message"],
        "--workdir does not exist or is not a directory: /nope"
    );
    assert!(export["session_id"].is_null());
    assert!(export["usage"].is_null());
}
