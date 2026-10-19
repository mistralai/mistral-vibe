//! Config diagnostics surfaced as toasts.

use std::collections::HashSet;

use serde_json::Value;

use crate::app::{App, Status, ToastSeverity};

/// Seconds a config-issue toast stays up.
const CONFIG_ISSUE_TOAST_SECS: u64 = 10;

/// Toast every skill/hook config issue and config validation warning in the startup snapshot, at readiness.
pub fn show_config_issues(app: &mut App, runtime: &Value) {
    if let Some(issues) = runtime.pointer("/runtime/issues").and_then(Value::as_array) {
        for issue in issues {
            let file = issue.get("file").and_then(Value::as_str).unwrap_or("");
            let message = issue.get("message").and_then(Value::as_str).unwrap_or("");
            app.show_toast(
                format!("{file}\n{message}"),
                ToastSeverity::Warning,
                CONFIG_ISSUE_TOAST_SECS,
            );
        }
    }
    show_config_validation_warnings(app, runtime);
}

/// Toast validation warnings that appear after startup, once the session is ready.
pub fn show_updated_config_warnings(app: &mut App, runtime: &Value) {
    // Pre-ready snapshots still carry the config a catalog fetch may resolve.
    if !matches!(
        app.session.status,
        Status::Ready | Status::Generating { .. }
    ) {
        return;
    }
    show_config_validation_warnings(app, runtime);
}

/// Toast each validation warning once; a warning that disappears is forgotten so it can toast again.
fn show_config_validation_warnings(app: &mut App, runtime: &Value) {
    let Some(warnings) = runtime
        .pointer("/runtime/config/validationWarnings")
        .and_then(Value::as_array)
    else {
        return;
    };
    let present: HashSet<&str> = warnings.iter().filter_map(Value::as_str).collect();
    app.shown_config_warnings
        .retain(|shown| present.contains(shown.as_str()));
    for warning in warnings.iter().filter_map(Value::as_str) {
        if !app.shown_config_warnings.insert(warning.to_owned()) {
            continue;
        }
        app.show_toast(
            warning.to_owned(),
            ToastSeverity::Warning,
            CONFIG_ISSUE_TOAST_SECS,
        );
    }
}
