//! Runtime-snapshot config issues surface as warning toasts (VIBE-4622),
//! validation warnings once the session is ready (VIBE-5122).

use std::time::Instant;

use serde_json::json;
use vibe_rs::app::{App, Status, ToastSeverity};
use vibe_rs::config_issues;

#[test]
fn skill_config_issue_shows_a_warning_toast() {
    let mut app = App::default();
    let runtime = json!({
        "runtime": {
            "issues": [
                {"file": "/proj/.vibe/skills/broken/SKILL.md", "message": "Failed to load: bad yaml"}
            ],
            "config": {"validationWarnings": []}
        }
    });

    config_issues::show_config_issues(&mut app, &runtime);

    let toast = app
        .overlays
        .toasts
        .back()
        .expect("config issue must surface a toast");
    assert_eq!(
        toast.text,
        "/proj/.vibe/skills/broken/SKILL.md\nFailed to load: bad yaml"
    );
    assert!(matches!(toast.severity, ToastSeverity::Warning));
}

#[test]
fn validation_warning_shows_a_warning_toast() {
    let mut app = App::default();
    let runtime = json!({
        "runtime": {
            "issues": [],
            "config": {"validationWarnings": ["deprecated field 'foo'"]}
        }
    });

    config_issues::show_config_issues(&mut app, &runtime);

    let toast = app
        .overlays
        .toasts
        .back()
        .expect("validation warning must surface a toast");
    assert_eq!(toast.text, "deprecated field 'foo'");
    assert!(matches!(toast.severity, ToastSeverity::Warning));
}

#[test]
fn no_issues_leaves_no_toast() {
    let mut app = App::default();
    let runtime = json!({"runtime": {"issues": [], "config": {"validationWarnings": []}}});

    config_issues::show_config_issues(&mut app, &runtime);

    assert!(app.overlays.toasts.is_empty());
}

#[test]
fn more_than_five_config_issues_are_retained() {
    let mut app = App::default();
    let issues: Vec<_> = (0..6)
        .map(|index| {
            json!({
                "file": format!("/proj/.vibe/skills/broken-{index}/SKILL.md"),
                "message": "Failed to load: bad yaml"
            })
        })
        .collect();
    let runtime = json!({
        "runtime": {
            "issues": issues,
            "config": {"validationWarnings": []}
        }
    });

    config_issues::show_config_issues(&mut app, &runtime);

    assert_eq!(app.overlays.toasts.len(), 6);
    assert!(app
        .overlays
        .toasts
        .front()
        .unwrap()
        .text
        .contains("broken-0"));
}

fn _runtime_with_warnings(warnings: &[&str]) -> serde_json::Value {
    json!({
        "runtime": {
            "issues": [],
            "config": {"validationWarnings": warnings}
        }
    })
}

#[test]
fn updated_warning_is_held_until_ready_then_toasts_once() {
    let mut app = App::default();
    let runtime = _runtime_with_warnings(&["Active model 'toto' is not configured"]);

    config_issues::show_updated_config_warnings(&mut app, &runtime);
    assert!(app.overlays.toasts.is_empty());

    app.set_status(Status::Ready);
    config_issues::show_updated_config_warnings(&mut app, &runtime);
    config_issues::show_updated_config_warnings(&mut app, &runtime);
    assert_eq!(app.overlays.toasts.len(), 1);
    assert_eq!(
        app.overlays.toasts.back().unwrap().text,
        "Active model 'toto' is not configured"
    );
}

#[test]
fn updated_warning_toasts_while_generating() {
    let mut app = App::default();
    app.set_status(Status::Generating {
        since: Instant::now(),
    });
    let runtime = _runtime_with_warnings(&["Active model 'toto' is not configured"]);

    config_issues::show_updated_config_warnings(&mut app, &runtime);

    assert_eq!(app.overlays.toasts.len(), 1);
}

#[test]
fn startup_warnings_are_not_retoasted_by_a_later_update() {
    let mut app = App::default();
    app.set_status(Status::Ready);
    let runtime = _runtime_with_warnings(&["Active model 'toto' is not configured"]);

    config_issues::show_config_issues(&mut app, &runtime);
    config_issues::show_updated_config_warnings(&mut app, &runtime);

    assert_eq!(app.overlays.toasts.len(), 1);
}

#[test]
fn a_warning_that_disappears_can_toast_again() {
    let mut app = App::default();
    app.set_status(Status::Ready);

    config_issues::show_updated_config_warnings(
        &mut app,
        &_runtime_with_warnings(&["Active model 'toto' is not configured"]),
    );
    config_issues::show_updated_config_warnings(&mut app, &_runtime_with_warnings(&[]));
    config_issues::show_updated_config_warnings(
        &mut app,
        &_runtime_with_warnings(&["Active model 'toto' is not configured"]),
    );

    assert_eq!(app.overlays.toasts.len(), 2);
}

#[test]
fn updated_warning_is_held_when_failed() {
    let mut app = App::default();
    app.set_status(Status::Failed);

    config_issues::show_updated_config_warnings(
        &mut app,
        &_runtime_with_warnings(&["Active model 'toto' is not configured"]),
    );

    assert!(app.overlays.toasts.is_empty());
}
