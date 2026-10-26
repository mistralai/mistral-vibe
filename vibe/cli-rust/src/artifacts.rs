//! Session artifacts (PR/MR): post-ready list fetch, live upsert, bar label.

use std::sync::Arc;

use serde_json::json;

use crate::app::App;
use crate::commands::CommandEvent;
use crate::server::{method, ArtifactsListResponse, Client, PublicSessionArtifact};

/// Fetch the session's artifacts once at ready; the bottom bar link renders
/// from the answer. A failed read is an empty list, never a toast.
pub fn fetch(app: &mut App, client: &Arc<Client>) {
    let (Some(session_id), Some(tx)) = (app.session.session_id.clone(), app.command_tx.clone())
    else {
        return;
    };
    // Hold the app busy until the answer lands, so a settled frame never
    // misses the link (same contract as `post_ready::fetch`).
    app.commit_started();
    let client = client.clone();
    tokio::spawn(async move {
        let response = client
            .request(method::ARTIFACTS_LIST, json!({ "sessionId": session_id }))
            .await;
        let artifacts = response
            .ok()
            .and_then(|value| serde_json::from_value::<ArtifactsListResponse>(value).ok())
            .map(|parsed| parsed.artifacts)
            .unwrap_or_default();
        let _ = tx.try_send(CommandEvent::ArtifactsRead(artifacts));
    });
}

/// Store the given artifacts, keeping every one, latest first.
pub fn apply_list(app: &mut App, artifacts: Vec<PublicSessionArtifact>) {
    for artifact in artifacts {
        upsert(app, artifact);
    }
}

/// Insert or replace by id (an `artifacts/updated` carries the same id), then
/// keep the list latest-first; both paths emit the same UTC ISO-8601 format,
/// so a string compare orders them.
pub fn upsert(app: &mut App, artifact: PublicSessionArtifact) {
    let list = &mut app.session.artifacts;
    match list.iter().position(|known| known.id == artifact.id) {
        Some(index) => list[index] = artifact,
        None => list.push(artifact),
    }
    list.sort_by(|a, b| b.discovered_at.cmp(&a.discovered_at));
}

/// The latest artifact (the list is kept latest-first), shown as the bar link.
pub fn latest(app: &App) -> Option<&PublicSessionArtifact> {
    app.session.artifacts.first()
}

/// The bottom-bar label: `PR #123` on GitHub, `MR !7` on GitLab.
pub fn label(artifact: &PublicSessionArtifact) -> String {
    let details = artifact.details.as_ref();
    match artifact.provider.as_str() {
        "gitlab" => match details.and_then(|details| details.merge_request_iid) {
            Some(iid) => format!("MR !{iid}"),
            None => "MR".to_owned(),
        },
        _ => match details.and_then(|details| details.pull_request_number) {
            Some(number) => format!("PR #{number}"),
            None => "PR".to_owned(),
        },
    }
}

/// The actionability status label (Phase 4): the lifecycle state for merged,
/// closed and draft PRs; the synced actionability status for open PRs.
/// `None` when there is nothing to show (an open PR not synced yet, or no
/// details) — the label stays bare, as before Phase 4.
pub fn status_label(artifact: &PublicSessionArtifact) -> Option<String> {
    let details = artifact.details.as_ref()?;
    match details.state.as_deref() {
        Some("merged") => return Some("merged".to_owned()),
        Some("closed") => return Some("closed".to_owned()),
        Some("draft") => return Some("draft".to_owned()),
        _ => {}
    }
    match details.pr_status.as_deref() {
        Some("action_needed") => Some("action needed".to_owned()),
        Some("waiting") => Some("waiting".to_owned()),
        Some("mergeable") => Some("mergeable".to_owned()),
        _ => None,
    }
}

/// The bar/modal label with the status appended: `PR #123 (mergeable)`,
/// `MR !7 (merged)`; bare `PR #123` when there is no status to show.
pub fn labeled(artifact: &PublicSessionArtifact) -> String {
    match status_label(artifact) {
        Some(status) => format!("{} ({status})", label(artifact)),
        None => label(artifact),
    }
}

/// The bar label when several artifacts share the one link slot: `PRs (2)`,
/// `MRs (3)`, or `PRs/MRs (2)` across providers.
pub fn count_label(artifacts: &[PublicSessionArtifact]) -> String {
    let mrs = artifacts.iter().filter(|a| a.provider == "gitlab").count();
    let prs = artifacts.len() - mrs;
    let kinds = match (prs > 0, mrs > 0) {
        (true, true) => "PRs/MRs",
        (true, false) => "PRs",
        _ => "MRs",
    };
    format!("{kinds} ({})", artifacts.len())
}

/// The artifacts modal's title: the provider(s) present, in full.
pub fn modal_title(artifacts: &[PublicSessionArtifact]) -> &'static str {
    let mrs = artifacts.iter().any(|a| a.provider == "gitlab");
    let prs = artifacts.iter().any(|a| a.provider != "gitlab");
    match (prs, mrs) {
        (true, true) => "Pull Requests / Merge Requests",
        (true, false) => "Pull Requests",
        (false, true) => "Merge Requests",
        (false, false) => "Artifacts",
    }
}
