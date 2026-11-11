//! The artifacts modal: every PR/MR artifact of the session as a link list,
//! opened from the bottom bar link or `/pr`.

use std::sync::Arc;

use serde_json::json;

use crate::app::{App, Severity};
use crate::artifacts;
use crate::external_url;
use crate::input_modes::InputMode;
use crate::server::{
    method, CheckOutcome, Client, PrStatus, PrStatusReason, PublicSessionArtifact,
};

/// How long the empty-list notice stays up.
const NOTICE_SECS: u64 = 4;

/// Open the modal highlighted on the latest artifact (the list is kept
/// latest-first).
pub fn open(app: &mut App) {
    app.artifacts_modal.list = crate::option_list::OptionList::new(0);
    app.artifacts_modal.open = true;
}

/// Open the modal for the user: mark every artifact seen and refresh the statuses.
pub fn show(app: &mut App, client: &Arc<Client>) {
    if app.session.session_id.is_none() {
        return;
    }
    if app.session.artifacts.is_empty() {
        crate::ui::notice::show(
            app,
            Severity::Information,
            "No pull requests in this session",
            NOTICE_SECS,
        );
        return;
    }
    open(app);
    let ids = app
        .session
        .artifacts
        .iter()
        .map(|artifact| artifact.id.clone())
        .collect();
    sync_statuses(app, client);
    mark_viewed(app, client, ids);
}

/// Refresh every status from its provider; the changes arrive as `artifacts/updated`.
pub fn sync_statuses(app: &App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    crate::work::spawn(async move {
        let params = json!({ "sessionId": session_id });
        let _ = client.request(method::ARTIFACTS_SYNC, params).await;
    });
}

/// Mark the `ids` artifacts seen; the server's copy follows as `artifacts/updated`.
pub fn mark_viewed(app: &mut App, client: &Arc<Client>, ids: Vec<String>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let now = time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_default();
    let mut unviewed = Vec::new();
    for artifact in &mut app.session.artifacts {
        if !ids.contains(&artifact.id) {
            continue;
        }
        app.session.artifacts_unseen.remove(&artifact.id);
        if artifact.viewed_at.is_none() {
            // Seen at once and never marked twice.
            artifact.viewed_at = Some(now.clone());
            unviewed.push(artifact.id.clone());
        }
    }
    if unviewed.is_empty() {
        return;
    }
    let client = client.clone();
    crate::work::spawn(async move {
        let params = json!({ "sessionId": session_id, "artifactIds": unviewed });
        let _ = client.request(method::ARTIFACTS_MARK_VIEWED, params).await;
    });
}

/// Close the modal without opening anything (Esc).
pub fn close(app: &mut App) {
    app.artifacts_modal.open = false;
}

/// Move the highlight up/down by one, wrapping at the ends.
pub fn navigate(app: &mut App, down: bool) {
    let count = option_count(app);
    app.artifacts_modal.list.step(down, count, true, |_| true);
}

/// PageUp/PageDown: move the highlight by one visible page, clamped to the list.
pub fn page(app: &mut App, down: bool) {
    let page = usize::from(app.artifacts_modal.list_area.height.max(1));
    let count = option_count(app);
    app.artifacts_modal.list.page(down, count, page);
}

/// Home/End: highlight the first or the last artifact.
pub fn edge(app: &mut App, end: bool) {
    let Some(last) = option_count(app).checked_sub(1) else {
        return;
    };
    app.artifacts_modal.list.select(if end { last } else { 0 });
}

/// The highlighted artifact, if any.
pub fn selected(app: &App) -> Option<&PublicSessionArtifact> {
    app.session
        .artifacts
        .get(app.artifacts_modal.list.selected())
}

/// Open the highlighted artifact's link and close the modal.
pub fn select(app: &mut App) {
    if let Some(artifact) = selected(app) {
        external_url::open(&artifact.external_url);
    }
    close(app);
}

/// Row count: one per artifact.
pub fn option_count(app: &App) -> usize {
    app.session.artifacts.len()
}

/// The highlighted artifact's first failing CI check with a link.
pub fn check_url(artifact: &PublicSessionArtifact) -> Option<&str> {
    let checks = &artifact.details.as_ref()?.checks.as_ref()?.checks;
    checks
        .iter()
        .filter(|check| check.status == Some(CheckOutcome::Fail))
        .find_map(|check| check.url.as_deref().filter(|url| !url.is_empty()))
}

/// Open the highlighted artifact's first CI check (`c`); the modal stays open.
pub fn open_check(app: &mut App) {
    if let Some(url) = selected(app).and_then(check_url) {
        external_url::open(url);
    }
}

/// The prompt asking the agent to unblock an open PR/MR that needs action; `None` otherwise.
pub fn fix_prompt(artifact: &PublicSessionArtifact) -> Option<String> {
    let details = artifact.details.as_ref()?;
    if details.pr_status != Some(PrStatus::ActionNeeded) {
        return None;
    }
    let pr = format!("{} ({})", artifacts::label(artifact), artifact.external_url);
    Some(match details.pr_status_reason {
        Some(PrStatusReason::ChangesRequested) => {
            format!("Address the changes requested in the review of {pr}.")
        }
        Some(PrStatusReason::ChecksFailing) => {
            let failed: Vec<&str> = details
                .checks
                .iter()
                .flat_map(|checks| &checks.checks)
                .filter(|check| check.status == Some(CheckOutcome::Fail))
                .filter_map(|check| check.name.as_deref())
                .collect();
            match failed.as_slice() {
                [] => format!("Fix the failing CI checks on {pr}."),
                names => format!("Fix the failing CI checks on {pr}: {}.", names.join(", ")),
            }
        }
        Some(PrStatusReason::Conflicts) => format!("Resolve the merge conflicts on {pr}."),
        _ => format!("Find out what blocks {pr} from merging and fix it."),
    })
}

/// Put the fix prompt for the highlighted artifact in the composer and close the modal (`f`).
pub fn fix(app: &mut App) {
    let Some(prompt) = selected(app).and_then(fix_prompt) else {
        return;
    };
    if app.chat_input.mode != InputMode::Prompt && !app.chat_input.input.is_empty() {
        crate::ui::notice::show(
            app,
            Severity::Information,
            "Clear the command draft first",
            NOTICE_SECS,
        );
        return;
    }
    crate::chat_input::append_prompt(app, &prompt);
    close(app);
}
