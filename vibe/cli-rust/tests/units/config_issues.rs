//! Startup runtime-snapshot config issues surface as warning toasts, matching
//! the legacy Python CLI's `_show_config_issues` (VIBE-4622).

use serde_json::json;
use vibe_rs::app::{App, ToastSeverity};
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
