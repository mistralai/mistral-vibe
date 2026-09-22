//! Startup config diagnostics (Python `App._show_config_issues`).

use serde_json::Value;

use crate::app::{App, ToastSeverity};

/// Seconds a config-issue toast stays up (Python `notify(timeout=10)`).
const CONFIG_ISSUE_TOAST_SECS: u64 = 10;

/// Toast every skill/hook config issue and config validation warning carried by
/// the startup runtime snapshot, matching Python's mount-time surfacing. Runs
/// once at readiness, not on every `runtime/updated`.
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
    if let Some(warnings) = runtime
        .pointer("/runtime/config/validationWarnings")
        .and_then(Value::as_array)
    {
        for warning in warnings {
            if let Some(text) = warning.as_str() {
                app.show_toast(
                    text.to_owned(),
                    ToastSeverity::Warning,
                    CONFIG_ISSUE_TOAST_SECS,
                );
            }
        }
    }
}
