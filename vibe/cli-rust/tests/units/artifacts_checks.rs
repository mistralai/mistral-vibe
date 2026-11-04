//! The artifacts modal's CI checks block: order, marks, colors and the summary line.

use serde_json::{json, Value};

use vibe_rs::server::PublicSessionArtifact;
use vibe_rs::ui::artifacts_checks::{lines, CheckLine, Outcome};
use vibe_rs::ui::theme;

fn with_checks(checks: &[(&str, &str)]) -> PublicSessionArtifact {
    let checks: Vec<Value> = checks
        .iter()
        .map(|(name, status)| json!({ "name": name, "status": status }))
        .collect();
    serde_json::from_value(json!({
        "id": "a1",
        "sessionId": "s",
        "kind": "pull_request",
        "provider": "github",
        "externalKey": "github.com/o/r/pull/1",
        "externalUrl": "https://github.com/o/r/pull/1",
        "discoveredAt": "2026-10-18T10:00:00+00:00",
        "details": { "pullRequestNumber": 1, "checks": { "state": "fail", "checks": checks } },
    }))
    .expect("artifact")
}

fn check(outcome: Outcome, name: &str) -> CheckLine {
    CheckLine::Check(outcome, name.to_owned())
}

#[test]
fn failures_come_first_then_pending_then_passed() {
    let artifact = with_checks(&[("build", "pass"), ("tests", "pending"), ("lint", "fail")]);
    assert_eq!(
        lines(Some(&artifact)),
        vec![
            check(Outcome::Failed, "lint"),
            check(Outcome::Pending, "tests"),
            check(Outcome::Passed, "build"),
        ]
    );
}

#[test]
fn the_hidden_checks_are_counted_with_their_shared_outcome() {
    let artifact = with_checks(&[
        ("build", "pass"),
        ("lint", "fail"),
        ("tests", "pending"),
        ("e2e", "pass"),
        ("deploy", "pass"),
    ]);
    let block = lines(Some(&artifact));
    assert_eq!(block.len(), 4);
    assert_eq!(block[0], check(Outcome::Failed, "lint"));
    assert_eq!(block[3], CheckLine::More("2 more passed".to_owned()));
}

#[test]
fn mixed_hidden_checks_are_only_counted() {
    let artifact = with_checks(&[
        ("a", "fail"),
        ("b", "fail"),
        ("c", "fail"),
        ("d", "pending"),
        ("e", "pass"),
    ]);
    let block = lines(Some(&artifact));
    assert_eq!(block[3], CheckLine::More("2 more".to_owned()));
}

#[test]
fn a_single_hidden_check_is_listed_instead_of_counted() {
    let artifact = with_checks(&[("a", "pass"), ("b", "pass"), ("c", "pass"), ("d", "pass")]);
    let block = lines(Some(&artifact));
    assert_eq!(block.len(), 4);
    assert!(block
        .iter()
        .all(|line| matches!(line, CheckLine::Check(..))));
}

#[test]
fn unknown_statuses_read_as_pending() {
    assert_eq!(Outcome::of(Some("skipping")), Outcome::Pending);
    assert_eq!(Outcome::of(None), Outcome::Pending);
}

#[test]
fn marks_use_the_footer_colors() {
    let (mark, style) = Outcome::Failed.mark();
    assert_eq!((mark, style.fg), ("✗", Some(theme::text_error())));
    let (mark, style) = Outcome::Passed.mark();
    assert_eq!((mark, style.fg), ("✓", Some(theme::text_success())));
    let (mark, style) = Outcome::Pending.mark();
    assert_eq!((mark, style.fg), ("○", theme::muted_style().fg));
}

#[test]
fn no_checks_means_no_block() {
    assert!(lines(None).is_empty());
    let artifact = with_checks(&[]);
    assert!(lines(Some(&artifact)).is_empty());
}
