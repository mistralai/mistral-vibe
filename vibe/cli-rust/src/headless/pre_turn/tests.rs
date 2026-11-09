use serde_json::json;

use super::{integrations_unavailable, startup_config_notices, OutputFormat};
use crate::server::{notification, Notification};

#[test]
fn startup_config_notices_reads_warnings_and_issues() {
    let runtime = json!({
        "runtime": {
            "config": { "validationWarnings": ["Settings in your config.toml that Vibe ignores: `modl`"] },
            "issues": [
                { "file": "/home/a/.vibe/config.toml", "message": "bad value" },
                { "file": "", "message": "no file" }
            ]
        }
    });
    let (warnings, issues) = startup_config_notices(&runtime);
    assert_eq!(
        warnings,
        ["Settings in your config.toml that Vibe ignores: `modl`"]
    );
    assert_eq!(issues, ["/home/a/.vibe/config.toml: bad value", "no file"]);
}

#[test]
fn startup_config_notices_tolerates_a_bare_snapshot() {
    let (warnings, issues) = startup_config_notices(&json!({}));
    assert!(warnings.is_empty());
    assert!(issues.is_empty());
}

fn warning(code: &str, message: &str) -> Notification {
    Notification {
        method: notification::WARNING.to_owned(),
        params: json!({"warning": {"code": code, "message": message}}),
    }
}

#[test]
fn only_integration_failures_become_run_warnings() {
    let settled = [
        warning("warning", "config is deprecated"),
        warning(
            "integrations_unavailable",
            "Couldn't load connectors GitHub: timed out.",
        ),
    ];

    let warnings = integrations_unavailable(&OutputFormat::Silent, &settled);

    let found: Vec<(&str, Option<&str>)> = warnings
        .iter()
        .map(|w| (w.message.as_str(), w.source.as_deref()))
        .collect();
    assert_eq!(
        found,
        [(
            "Couldn't load connectors GitHub: timed out.",
            Some("integrations_unavailable")
        )]
    );
}
