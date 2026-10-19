//! Startup banner mounts: what's-new, the VS Code extension promo, the
//! custom-tools deprecation, and the untrusted-config warning.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::commands::CommandEvent;
use crate::server::{method, Client, WorkspaceUntrustedConfigResponse};
use crate::terminal_detect;
use crate::transcript::local;
use crate::utils::{cache_store, whats_new_cache};

/// The shipped what's-new body. The
/// `VIBE_WHATS_NEW_FILE` env var (test-only) reads a runtime file instead of
/// the compile-time body; missing, unreadable, or empty content skips the
/// banner.
const WHATS_NEW_MD: &str = include_str!("../../../whats_new.md");
const WHATS_NEW_FILE_ENV: &str = "VIBE_WHATS_NEW_FILE";
const VSCODE_EXTENSION_URI: &str = "vscode:extension/mistralai.mistral-vibe-code";
const VSCODE_EXTENSION_LINK_LABEL: &str = "VS Code extension";
/// The VS Code extension promo start: 2026-05-28 16:00 UTC.
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
    // The deprecation is checked once per session after the initial history,
    // whatever session the handshake attached; a later resume rebuild re-checks.
    app.session.runtime = runtime.clone();
    show_custom_tools_deprecation(app, runtime);
    fetch_untrusted_config(app, client);
}

/// The what's-new body to mount, or `None` to skip the banner (missing,
/// unreadable, or empty after trimming).
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
    let Some(body) = whats_new_body() else {
        // Fall back to the standalone promo and still mark the version as seen
        // when the what's-new body is absent.
        if promo_eligible {
            app.view.promo = Some(promo_standalone());
            record_promo_shown();
        }
        whats_new_cache::mark_seen(env!("CARGO_PKG_VERSION"));
        return;
    };
    let after_history = app.view.transcript.has_server_entries();
    if promo_eligible {
        record_promo_shown();
    }
    let whats_new = WhatsNew {
        id: new_message_id(),
        body,
        promo: promo_eligible,
    };
    local::add_whats_new(
        &mut app.view.transcript,
        &whats_new.id,
        &whats_new.text(None),
        after_history,
    );
    app.session.whats_new = Some(whats_new);
    whats_new_cache::mark_seen(env!("CARGO_PKG_VERSION"));
}

/// The mounted what's-new entry and the parts its text is rebuilt from.
pub struct WhatsNew {
    pub id: String,
    pub body: String,
    pub promo: bool,
}

impl WhatsNew {
    /// The body, then the plan offer, then the promo suffix.
    fn text(&self, plan_offer: Option<&str>) -> String {
        let mut text = self.body.clone();
        if let Some(cta) = plan_offer {
            text.push_str("\n\n");
            text.push_str(cta);
        }
        if self.promo {
            text.push_str(&promo_whats_new_suffix());
        }
        text
    }
}

/// Rebuild the mounted what's-new text with the account's plan offer: the
/// account read lands after the body mounts.
pub fn show_plan_offer(app: &mut App, account: &Value) {
    let (Some(whats_new), Some(cta)) = (&app.session.whats_new, plan_offer_cta(account)) else {
        return;
    };
    local::set_text(
        &mut app.view.transcript,
        &whats_new.id,
        &whats_new.text(Some(&cta)),
    );
}

/// The what's-new call to action for `AccountView.planOffer`, if any.
fn plan_offer_cta(account: &Value) -> Option<String> {
    let offer = account.get("planOffer")?;
    let url = offer.get("url")?.as_str()?;
    Some(match offer.get("kind").and_then(Value::as_str) {
        Some("switch_api_key") => format!("### Switch to your [Vibe Pro API key]({url})"),
        _ => format!("### Unlock more with Vibe - [Upgrade to Vibe Pro]({url})"),
    })
}

/// The first submit removes the what's-new body, so it does not stay pinned
/// below the conversation it started.
pub fn dismiss_whats_new(app: &mut App) {
    if let Some(whats_new) = app.session.whats_new.take() {
        app.view.transcript.remove(&whats_new.id);
    }
}

/// Once per session, whenever the runtime lists a custom tool; the mounted
/// entry's id guards against showing it twice.
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

/// A transcript rebuild drops the mounted message and re-decides it against
/// the latest known runtime, so a resume re-shows the banner for the session
/// it landed on.
pub fn rebuild_custom_tools_deprecation(app: &mut App) {
    if let Some(id) = app.session.custom_tools_deprecation_id.take() {
        app.view.transcript.remove(&id);
    }
    let runtime = app.session.runtime.clone();
    show_custom_tools_deprecation(app, &runtime);
}

/// The `runtime.tools` entries flagged `isCustom` (app-server `ToolSummary.is_custom`).
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

/// Promo gate: a VS Code-family terminal, past the start date, under the display cap.
fn should_show_promo() -> bool {
    terminal_detect::is_vscode_family(terminal_detect::detect())
        && SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .is_ok_and(|now| now.as_secs() >= PROMO_START)
        && cache_store::read_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY).unwrap_or(0)
            < MAX_SHOWN_COUNT
}

/// Count one more promo display.
fn record_promo_shown() {
    let previous = cache_store::read_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY).unwrap_or(0);
    cache_store::write_int(PROMO_CACHE_SECTION, PROMO_CACHE_KEY, previous + 1);
}

/// Ask once for the ignored config folders, off the startup path; the
/// warning mounts when the answer lands.
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

/// The untrusted-config warning's gate and body: warn once per folder,
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

/// The warning body, one `• {dir}` line per folder.
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
mod tests;
