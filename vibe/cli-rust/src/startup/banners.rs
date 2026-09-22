//! Startup banner mounts: what's-new, the VS Code extension promo, the
//! custom-tools deprecation, and the untrusted-config warning
//! (Python `_check_and_show_whats_new`, `_show_custom_tools_deprecation_warning`,
//! `_show_untrusted_config_warning`).

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::commands::CommandEvent;
use crate::server::{method, Client, WorkspaceUntrustedConfigResponse};
use crate::transcript::local;
use crate::utils::{cache_store, whats_new_cache};

/// Python `load_whats_new_content`: the shipped what's-new body. The
/// `VIBE_WHATS_NEW_FILE` env var (test-only) reads a runtime file instead of
/// the compile-time body; missing, unreadable, or empty content skips the
/// banner, mirroring Python's `None`.
const WHATS_NEW_MD: &str = include_str!("../../../whats_new.md");
const WHATS_NEW_FILE_ENV: &str = "VIBE_WHATS_NEW_FILE";
const VSCODE_EXTENSION_URI: &str = "vscode:extension/mistralai.mistral-vibe-code";
const VSCODE_EXTENSION_LINK_LABEL: &str = "VS Code extension";
/// Python `PROMO_START`: 2026-05-28 16:00 UTC.
const PROMO_START: u64 = 1_779_984_000;
const MAX_SHOWN_COUNT: i64 = 10;
const PROMO_CACHE_SECTION: &str = "vscode_extension_promo";
const PROMO_CACHE_KEY: &str = "shown_count";
const UNTRUSTED_WARNING_SECTION: &str = "untrusted_config_warning";
const UNTRUSTED_WARNING_KEY: &str = "dirs";

/// Mount the post-ready banners once the session history is loaded.
pub fn mount(app: &mut App, runtime: &Value, client: &Arc<Client>) {
    let promo_eligible = should_show_promo();
    if whats_new_cache::should_show(env!("CARGO_PKG_VERSION")) {
        mount_whats_new(app, promo_eligible);
    } else if promo_eligible {
        app.view.promo = Some(promo_standalone());
        record_promo_shown();
    }
    // Python checks the deprecation once per session after the initial history,
    // whatever session the handshake attached; a later resume rebuild re-checks.
    app.session.runtime = runtime.clone();
    show_custom_tools_deprecation(app, runtime);
    fetch_untrusted_config(app, client);
}

/// Python `load_whats_new_content`: the body to mount, or `None` to skip the
/// banner (missing, unreadable, or empty after trimming).
fn whats_new_body() -> Option<String> {
    if let Ok(path) = std::env::var(WHATS_NEW_FILE_ENV) {
        return std::fs::read_to_string(path)
            .ok()
            .as_deref()
            .and_then(non_empty);
    }
    non_empty(WHATS_NEW_MD)
}

fn non_empty(body: &str) -> Option<String> {
    let trimmed = body.trim();
    (!trimmed.is_empty()).then(|| trimmed.to_owned())
}

fn mount_whats_new(app: &mut App, promo_eligible: bool) {
    let Some(mut body) = whats_new_body() else {
        // Python falls back to the standalone promo and still marks the
        // version as seen when the what's-new body is absent.
        if promo_eligible {
            app.view.promo = Some(promo_standalone());
            record_promo_shown();
        }
        whats_new_cache::mark_seen(env!("CARGO_PKG_VERSION"));
        return;
    };
    let after_history = app.view.transcript.has_server_entries();
    if promo_eligible {
        body.push_str(&promo_whats_new_suffix());
        record_promo_shown();
    }
    local::add_whats_new(
        &mut app.view.transcript,
        &new_message_id(),
        &body,
        after_history,
    );
    whats_new_cache::mark_seen(env!("CARGO_PKG_VERSION"));
}

/// Python `_show_custom_tools_deprecation_warning`: once per session, whenever
/// the runtime lists a custom tool. Python keeps the mounted message as its
/// guard; the entry id plays that role here.
pub fn show_custom_tools_deprecation(app: &mut App, runtime: &Value) {
    if app.session.custom_tools_deprecation_id.is_some() {
        return;
    }
    let names = custom_tool_names(runtime);
    if names.is_empty() {
        return;
    }
    let id = new_message_id();
    local::add_custom_tools_deprecation(&mut app.view.transcript, &id, &names);
    app.session.custom_tools_deprecation_id = Some(id);
}

/// Python `_rebuild_transcript_from_current_session`: the rebuild drops the
/// mounted message and re-decides it against the latest known runtime, so a
/// resume re-shows the banner for the session it landed on.
pub fn rebuild_custom_tools_deprecation(app: &mut App) {
    if let Some(id) = app.session.custom_tools_deprecation_id.take() {
        app.view.transcript.remove(&id);
    }
    let runtime = app.session.runtime.clone();
    show_custom_tools_deprecation(app, &runtime);
}

/// The `runtime.tools` entries flagged `isCustom` (Python `ToolSummary.is_custom`).
fn custom_tool_names(runtime: &Value) -> Vec<String> {
    runtime
        .pointer("/runtime/tools")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|tool| tool.get("isCustom").and_then(Value::as_bool) == Some(true))
        .filter_map(|tool| tool.get("name").and_then(Value::as_str).map(str::to_owned))
        .collect()
}

fn promo_standalone() -> String {
    format!(
        "We now have a [{VSCODE_EXTENSION_LINK_LABEL}]({VSCODE_EXTENSION_URI}) with a rich UI. Check it out!"
    )
}

fn promo_whats_new_suffix() -> String {
    format!(
        "\n\n_Btw, we also have a new [{VSCODE_EXTENSION_LINK_LABEL}]({VSCODE_EXTENSION_URI}). Check it out!_"
    )
}

/// Python `should_show_promo` and `_is_vscode_family_terminal`: every
/// `TERM_PROGRAM=vscode` variant (VS Code, Insiders, Cursor) lands in the
/// family, so the terminal check reduces to the program name.
fn should_show_promo() -> bool {
    std::env::var("TERM_PROGRAM").is_ok_and(|program| program.to_lowercase() == "vscode")
        && SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .is_ok_and(|now| now.as_secs() >= PROMO_START)
        && cache_store::read_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY).unwrap_or(0)
            < MAX_SHOWN_COUNT
}

/// Python `_record_vscode_extension_promo_shown`: count one more display.
fn record_promo_shown() {
    let previous = cache_store::read_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY).unwrap_or(0);
    cache_store::write_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY, previous + 1);
}

/// Ask once for the ignored config folders, off the startup path like Python's
/// worker; the warning mounts when the answer lands.
fn fetch_untrusted_config(app: &mut App, client: &Arc<Client>) {
    let (Some(cwd), Some(tx)) = (app.session.cwd.clone(), app.command_tx.clone()) else {
        return;
    };
    app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let response = client
            .request(
                method::WORKSPACE_TRUST_UNTRUSTED_CONFIG,
                json!({ "cwd": cwd }),
            )
            .await;
        let warning = response
            .ok()
            .and_then(|response| untrusted_warning(&response));
        let _ = tx.try_send(CommandEvent::UntrustedConfig(warning));
    });
}

/// Python `_show_untrusted_config_warning`'s gate and body: warn once per folder,
/// still surfacing new ones, then record the folders as acknowledged.
fn untrusted_warning(response: &Value) -> Option<String> {
    let parsed: WorkspaceUntrustedConfigResponse = serde_json::from_value(response.clone()).ok()?;
    if parsed.dirs.is_empty() {
        return None;
    }
    let acknowledged =
        cache_store::read_string_list(UNTRUSTED_WARNING_SECTION, UNTRUSTED_WARNING_KEY)
            .unwrap_or_default();
    if parsed.dirs.iter().all(|dir| acknowledged.contains(dir)) {
        return None;
    }
    let mut dirs = acknowledged;
    dirs.extend(parsed.dirs.iter().cloned());
    dirs.sort();
    dirs.dedup();
    cache_store::write_string_list(UNTRUSTED_WARNING_SECTION, UNTRUSTED_WARNING_KEY, &dirs);
    Some(warning_text(&parsed.dirs, &parsed.settings_path))
}

/// The `WarningMessage` body Python mounts, one `• {dir}` line per folder.
fn warning_text(dirs: &[String], settings_path: &str) -> String {
    let folders = dirs
        .iter()
        .map(|dir| format!("  • {dir}"))
        .collect::<Vec<_>>()
        .join("\n");
    format!(
        "⚠ Untrusted local config folders are being ignored:\n\n{folders}\n\nIf you want them loaded, remove them from \"untrusted\" in {settings_path}, or ask Vibe to do it."
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn custom_tool_names_pick_is_custom_entries() {
        let runtime = json!({"runtime": {"tools": [
            {"name": "skill", "isCustom": false},
            {"name": "legacy_lint", "isCustom": true},
            {"name": "anon_custom"},
        ]}});
        assert_eq!(custom_tool_names(&runtime), vec!["legacy_lint".to_owned()]);
        assert_eq!(custom_tool_names(&json!({})), Vec::<String>::new());
    }

    #[test]
    fn untrusted_warning_names_every_ignored_folder() {
        let dirs = vec![
            "/home/user/repo/.vibe".to_owned(),
            "/home/user/other/.vibe".to_owned(),
        ];
        let text = warning_text(&dirs, "/home/user/.vibe/trusted_folders.toml");
        assert!(text.starts_with("⚠ Untrusted local config folders are being ignored:\n\n"));
        assert!(text.contains("\n\n  • /home/user/repo/.vibe\n  • /home/user/other/.vibe\n\n"));
        assert!(text.ends_with(
            "If you want them loaded, remove them from \"untrusted\" in /home/user/.vibe/trusted_folders.toml, or ask Vibe to do it."
        ));
    }

    #[test]
    fn an_empty_dir_list_never_warns() {
        assert_eq!(
            untrusted_warning(&json!({"dirs": [], "settingsPath": "p"})),
            None
        );
    }

    #[test]
    fn whats_new_body_env_override_mirrors_python_none_cases() {
        let dir =
            std::env::temp_dir().join(format!("vibe-rs-whatsnew-body-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let fixture = dir.join("whats_new.fixture");

        std::fs::write(&fixture, "  # What's new in v9.9.9-fixture  \n").unwrap();
        std::env::set_var(WHATS_NEW_FILE_ENV, &fixture);
        assert_eq!(
            whats_new_body().as_deref(),
            Some("# What's new in v9.9.9-fixture")
        );

        std::fs::write(&fixture, "   \n\t  \n").unwrap();
        assert_eq!(whats_new_body(), None);

        std::env::set_var(WHATS_NEW_FILE_ENV, dir.join("missing.fixture"));
        assert_eq!(whats_new_body(), None);

        std::env::remove_var(WHATS_NEW_FILE_ENV);
        assert_eq!(
            whats_new_body().as_deref(),
            non_empty(WHATS_NEW_MD).as_deref()
        );

        let _ = std::fs::remove_dir_all(&dir);
    }
}
