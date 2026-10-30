//! Config diagnostics surfaced as toasts.

use std::collections::HashSet;

use serde_json::Value;

use crate::app::{App, Status, ToastSeverity};

/// Seconds a config-issue toast stays up.
const CONFIG_ISSUE_TOAST_SECS: u64 = 10;

/// Toast every skill/hook config issue and config validation warning in the startup snapshot, at readiness.
pub fn show_config_issues(app: &mut App, runtime: &Value) {
    for issue in crate::server::RuntimeSnapshot::read(runtime)
        .unwrap_or_default()
        .issues
    {
        let message = format!("{}\n{}", issue.file, issue.message);
        app.show_toast(message, ToastSeverity::Warning, CONFIG_ISSUE_TOAST_SECS);
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
    let Some(config) = crate::server::RuntimeSnapshot::config(runtime) else {
        return;
    };
    let warnings = config.validation_warnings;
    let present: HashSet<&String> = warnings.iter().collect();
    app.shown_config_warnings
        .retain(|shown| present.contains(shown));
    for warning in warnings {
        if app.shown_config_warnings.insert(warning.clone()) {
            app.show_toast(warning, ToastSeverity::Warning, CONFIG_ISSUE_TOAST_SECS);
        }
    }
}
