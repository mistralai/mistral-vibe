//! Startup banner mounts: what's-new, the VS Code extension promo, and
//! the untrusted-config warning.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Deserialize;
use serde_json::{json, Value};

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::commands::CommandEvent;
use crate::server::{method, Client, WorkspaceUntrustedConfigResponse};
use crate::terminal_detect;
use crate::transcript::local;
use crate::utils::cache_store;
use crate::utils::whats_new_cache::{self, LastSeen};
use crate::whats_new;

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
    let current = env!("CARGO_PKG_VERSION");
    // A resumed conversation stays last on screen: what's new waits for a fresh session.
    let fresh = !app.view.transcript.has_server_entries() && !app.session.resume_picker_shown;
    let seen = match whats_new_cache::last_seen() {
        LastSeen::Seen(seen) if fresh && seen.as_deref() != Some(current) => Some(seen),
        _ => None,
    };
    if let Some(seen) = seen {
        mount_whats_new(app, seen.as_deref(), promo_eligible);
        whats_new_cache::mark_seen(current);
    } else if promo_eligible {
        app.view.promo = Some(promo_standalone());
        record_promo_shown();
    }
    app.session.runtime = runtime.clone();
    fetch_untrusted_config(app, client);
}

/// Mount every release's notes newer than `seen`; with none, the promo shows alone.
fn mount_whats_new(app: &mut App, seen: Option<&str>, promo_eligible: bool) {
    let entries = whats_new::entries();
    let Some(body) = whats_new::startup_body(whats_new::unseen(&entries, seen)) else {
        if promo_eligible {
            app.view.promo = Some(promo_standalone());
            record_promo_shown();
        }
        return;
    };
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
    );
    app.session.whats_new = Some(whats_new);
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
pub fn untrusted_warning(response: &Value) -> Option<String> {
    let parsed = WorkspaceUntrustedConfigResponse::deserialize(response).ok()?;
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
