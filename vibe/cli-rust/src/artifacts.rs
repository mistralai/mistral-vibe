//! Session artifacts (PR/MR): list read per session, live upsert, bar label.

use std::borrow::Borrow;
use std::sync::Arc;

use serde_json::json;

use crate::app::App;
use crate::commands::CommandEvent;
use crate::server::{
    method, ArtifactsListResponse, Client, PrState, PrStatus, PrStatusReason, PublicSessionArtifact,
};

/// Read the session's artifacts; the bottom bar link renders from the
/// answer. A failed read is an empty list, never a notice.
pub fn fetch(app: &mut App, client: &Arc<Client>) {
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    app.session.artifacts_session = Some(session_id.clone());
    let tx = app.command_tx.clone();
    // Hold the app busy until the answer lands, so a settled frame never
    // misses the link (same contract as `post_ready::fetch`).
    let client = client.clone();
    crate::work::spawn(async move {
        let response = client
            .request(method::ARTIFACTS_LIST, json!({ "sessionId": session_id }))
            .await;
        let artifacts = response
            .ok()
            .and_then(|value| serde_json::from_value::<ArtifactsListResponse>(value).ok())
            .map(|parsed| parsed.artifacts)
            .unwrap_or_default();
        let read = CommandEvent::ArtifactsRead {
            session_id,
            artifacts,
        };
        crate::input::deliver(tx, read).await;
    });
}

/// Read the artifacts of a session adopted since the last read; `App::set_session_id` already dropped the old ones.
pub fn follow_session(app: &mut App, client: &Arc<Client>) {
    if app.session.artifacts_session == app.session.session_id {
        return;
    }
    fetch(app, client);
}

/// Store a read's artifacts, latest first, unless the session moved on since.
pub fn apply_list(app: &mut App, session_id: &str, artifacts: Vec<PublicSessionArtifact>) {
    if app.session.session_id.as_deref() != Some(session_id) {
        return;
    }
    for artifact in artifacts {
        upsert(app, artifact);
    }
}

/// Apply an `artifacts/created` or `artifacts/updated` notification.
pub fn on_notification(
    app: &mut App,
    client: &Arc<Client>,
    method: &str,
    artifact: PublicSessionArtifact,
) {
    // A PR/MR created while the user looks at the list is seen as it lands.
    let seen = (method == crate::server::notification::ARTIFACTS_CREATED
        && artifact.viewed_at.is_none()
        && app.artifacts_modal.open)
        .then(|| artifact.id.clone());
    upsert(app, artifact);
    if let Some(id) = seen {
        crate::artifacts_modal::mark_viewed(app, client, vec![id]);
    }
}

/// Insert or replace by PR identity (`externalKey`; the record id differs across sessions and records), then
/// keep the list latest-first; both paths emit the same UTC ISO-8601 format,
/// so a string compare orders them. An open modal keeps its highlighted artifact.
pub fn upsert(app: &mut App, mut artifact: PublicSessionArtifact) {
    let highlighted = crate::artifacts_modal::selected(app).map(|known| known.external_key.clone());
    let seen = app.artifacts_modal.open;
    let list = &mut app.session.artifacts;
    let same = |known: &PublicSessionArtifact| {
        known.id == artifact.id || known.external_key == artifact.external_key
    };
    match list.iter().position(same) {
        Some(index) => {
            // A status change the user has not looked at yet marks the link until the modal opens.
            if !seen && labeled(&list[index]) != labeled(&artifact) {
                app.session.artifacts_unseen.insert(artifact.id.clone());
            }
            // A read raced by `markViewed` must not unmark the PR/MR.
            if artifact.viewed_at.is_none() {
                artifact.viewed_at = list[index].viewed_at.take();
            }
            list[index] = artifact;
        }
        None => list.push(artifact),
    }
    list.sort_by(|a, b| b.discovered_at.cmp(&a.discovered_at));
    if let Some(index) =
        highlighted.and_then(|key| list.iter().position(|known| known.external_key == key))
    {
        app.artifacts_modal.list.highlight(index);
    }
}

/// The latest artifact (the list is kept latest-first), shown as the bar link.
pub fn latest(app: &App) -> Option<&PublicSessionArtifact> {
    app.session.artifacts.first()
}

/// Whether the PR/MR is still in play: neither merged nor closed.
pub fn is_active(artifact: &PublicSessionArtifact) -> bool {
    !matches!(state(artifact), Some(PrState::Merged | PrState::Closed))
}

/// The synced lifecycle state, `None` before the first sync.
pub fn state(artifact: &PublicSessionArtifact) -> Option<PrState> {
    artifact.details.as_ref()?.state
}

/// The server's actionability status, `None` for merged, closed and draft PRs or before a sync.
pub fn pr_status(artifact: &PublicSessionArtifact) -> Option<PrStatus> {
    artifact.details.as_ref()?.pr_status
}

/// Why the status is what it is, `None` when the server does not say.
pub fn reason(artifact: &PublicSessionArtifact) -> Option<PrStatusReason> {
    artifact.details.as_ref()?.pr_status_reason
}

/// The artifacts the bar shows: the active ones, or all of them once none is active.
pub fn bar_artifacts(app: &App) -> Vec<&PublicSessionArtifact> {
    let active: Vec<_> = app
        .session
        .artifacts
        .iter()
        .filter(|a| is_active(a))
        .collect();
    if active.is_empty() {
        app.session.artifacts.iter().collect()
    } else {
        active
    }
}

/// Never opened in the modal, or its status changed since the modal was last open.
pub fn is_unseen(app: &App, artifact: &PublicSessionArtifact) -> bool {
    artifact.viewed_at.is_none() || app.session.artifacts_unseen.contains(&artifact.id)
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

/// The narrowest bar label: `#123` or `!7`, falling back to the full label.
pub fn short_label(artifact: &PublicSessionArtifact) -> String {
    let details = artifact.details.as_ref();
    match (
        details.and_then(|details| details.merge_request_iid),
        details.and_then(|details| details.pull_request_number),
    ) {
        (Some(iid), _) if artifact.provider == "gitlab" => format!("!{iid}"),
        (_, Some(number)) if artifact.provider != "gitlab" => format!("#{number}"),
        _ => label(artifact),
    }
}

/// How a PR/MR stands, for the bar's state dot.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Standing {
    Ready,
    Blocked,
    Merged,
    Pending,
}

/// Read from the wire state and synced status, not the display label.
pub fn standing(artifact: &PublicSessionArtifact) -> Standing {
    match (state(artifact), pr_status(artifact)) {
        (Some(PrState::Merged), _) => Standing::Merged,
        (Some(PrState::Closed), _) | (Some(PrState::Open) | None, Some(PrStatus::ActionNeeded)) => {
            Standing::Blocked
        }
        (Some(PrState::Open) | None, Some(PrStatus::Mergeable)) => Standing::Ready,
        _ => Standing::Pending,
    }
}

/// The status label: the lifecycle state for merged, closed and draft PRs;
/// for open PRs the synced status, named by its cause when known (`CI failing`).
/// `None` when there is nothing to show (an open PR not synced yet, or no details).
pub fn status_label(artifact: &PublicSessionArtifact) -> Option<String> {
    let label = match (state(artifact), pr_status(artifact)) {
        (Some(PrState::Merged), _) => "merged",
        (Some(PrState::Closed), _) => "closed",
        (Some(PrState::Draft), _) => "draft",
        (_, Some(PrStatus::ActionNeeded)) => match reason(artifact) {
            Some(PrStatusReason::ChangesRequested) => "changes requested",
            Some(PrStatusReason::ChecksFailing) => "CI failing",
            Some(PrStatusReason::Conflicts) => "conflicts",
            _ => "action needed",
        },
        (_, Some(PrStatus::Waiting)) => match reason(artifact) {
            Some(PrStatusReason::ChecksPending) => "CI running",
            Some(PrStatusReason::ReviewRequired) => "review required",
            _ => "waiting",
        },
        (_, Some(PrStatus::Mergeable)) => "mergeable",
        _ => return None,
    };
    Some(label.to_owned())
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
pub fn count_label<A: Borrow<PublicSessionArtifact>>(artifacts: &[A]) -> String {
    let mrs = artifacts
        .iter()
        .filter(|a| Borrow::<PublicSessionArtifact>::borrow(*a).provider == "gitlab")
        .count();
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
