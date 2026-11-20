//! Deleting sessions through `storage/sessions/delete`: the estimate shown, the request, and its result rows.

use std::collections::HashSet;
use std::sync::Arc;

use super::actions::{plural, status, title_of};
use super::leftovers::kept_text;
use super::model::{space, Estimate, Space};
use super::outcome::{apply_answer, kept_row};
use super::{report, request, View};
use crate::app::App;
use crate::server::{Client, StorageSessionsDeleteResponse, StorageSnapshot};

/// A delete waiting for the server's answer: what it started from, for the rows and telemetry.
pub(super) struct Pending {
    /// `Some` for a single row: its title.
    single: Option<String>,
    suggested: usize,
    selected: Vec<String>,
    unselected: Vec<String>,
    estimate: Option<Estimate>,
    measured_at: u64,
}

/// What deleting `selected` frees: the server's suggestion total when every suggested session is
/// selected, else the selected rows' `deleteBytes`, a floor once several share a worktree or one is
/// not measured; `None` when no selected row's size is known.
pub fn estimate(snapshot: &StorageSnapshot, selected: &HashSet<String>) -> Option<Estimate> {
    let suggested = &snapshot.delete_suggestion;
    let all: HashSet<&String> = suggested.session_ids.iter().collect();
    if !all.is_empty() && all.len() == selected.len() && selected.iter().all(|id| all.contains(id))
    {
        return Some(Estimate {
            bytes: suggested.reclaimable_bytes,
            at_least: suggested.reclaimable_at_least,
        });
    }
    let rows: Vec<_> = snapshot
        .sessions
        .iter()
        .filter(|s| selected.contains(&s.session_id))
        .collect();
    let known: Vec<_> = rows.iter().filter(|s| s.delete_bytes.is_some()).collect();
    if known.is_empty() {
        return None;
    }
    Some(Estimate {
        bytes: known.iter().filter_map(|s| s.delete_bytes).sum(),
        at_least: rows.len() > 1 || rows.iter().any(|s| s.delete_bytes_at_least),
    })
}

/// What deleting one row frees, from its `deleteBytes`; `None` when unknown.
pub fn row_estimate(session: &crate::server::StorageSession) -> Option<Estimate> {
    session.delete_bytes.map(|bytes| Estimate {
        bytes,
        at_least: session.delete_bytes_at_least,
    })
}

/// `y` on a row's delete or on the delete review: one `storage/sessions/delete`, never an empty one.
pub(super) fn start(app: &mut App, client: &Arc<Client>, single: Option<(String, String)>) {
    let ids = begin(app, single);
    if !ids.is_empty() {
        request::delete_sessions(app, client, ids);
    }
}

/// Mark the delete in flight and return the ids to send: the row's, else the review's selection in
/// review order; empty, with nothing in flight, when nothing is selected.
pub fn begin(app: &mut App, single: Option<(String, String)>) -> Vec<String> {
    let measured_at = app
        .storage
        .usage
        .snapshot
        .as_ref()
        .map_or(0, |s| s.measured_at);
    let pending = match single {
        Some((id, title)) => {
            app.storage.busy = Some(format!("Deleting \"{title}\"…"));
            Pending {
                single: Some(title),
                suggested: 0,
                selected: vec![id],
                unselected: Vec::new(),
                estimate: None,
                measured_at,
            }
        }
        None => {
            let review = &app.storage.review;
            let (selected, unselected): (Vec<String>, Vec<String>) = review
                .order
                .iter()
                .cloned()
                .partition(|id| review.selected.contains(id));
            let snapshot = app.storage.usage.snapshot.as_ref();
            let estimate = snapshot.and_then(|s| estimate(s, &review.selected));
            let suggested = review.order.len();
            let unselected = unselected.iter().map(|id| title_of(app, id)).collect();
            let count = plural(selected.len(), "session", "sessions");
            app.storage.busy = Some(format!("Deleting {count}…"));
            Pending {
                single: None,
                suggested,
                selected,
                unselected,
                estimate,
                measured_at,
            }
        }
    };
    let ids = pending.selected.clone();
    if ids.is_empty() {
        app.storage.busy = None;
        return ids;
    }
    app.storage.deleting = Some(pending);
    ids
}

/// The server answered: write the result rows, drop what went, and wait for the refresh it started.
pub fn deleted(app: &mut App, answer: Result<StorageSessionsDeleteResponse, String>) {
    app.storage.busy = None;
    let Some(pending) = app.storage.deleting.take() else {
        return;
    };
    app.storage.view = View::Panel;
    let answer = match answer {
        Ok(answer) => answer,
        Err(error) => {
            return crate::notices::failed(app, &format!("Failed to delete: {error}"));
        }
    };
    if pending.single.is_none() {
        report::bulk_delete(
            app,
            &report::BulkDeleteCounts {
                suggested: pending.suggested,
                selected: pending.selected.len(),
                deleted: answer.removed.len(),
                refused: answer.kept.len(),
                estimated: pending.estimate.map_or(0, |e| e.bytes),
                measured: answer.freed_bytes,
            },
        );
    }
    for text in result_rows(app, &pending, &answer) {
        status(app, &text);
    }
    drop_deleted(app, &answer, pending.measured_at);
}

/// What the server says the delete freed: a floor when a removed worktree was not measured yet.
pub fn freed(answer: &StorageSessionsDeleteResponse) -> Estimate {
    Estimate {
        bytes: answer.freed_bytes,
        at_least: answer.freed_bytes_at_least,
    }
}

/// `Deleted 2 sessions. Freed about 270 MB.`, then what was kept and why.
fn result_rows(
    app: &App,
    pending: &Pending,
    answer: &StorageSessionsDeleteResponse,
) -> Vec<String> {
    let mut rows = Vec::new();
    let freed = space(Space::Freed, Some(freed(answer)));
    match (&pending.single, answer.removed.len()) {
        (_, 0) => {}
        (Some(title), _) => rows.push(format!("Deleted \"{title}\". {freed}")),
        (None, n) => rows.push(format!(
            "Deleted {}. {freed}",
            plural(n, "session", "sessions")
        )),
    }
    let sessions: Vec<(String, &str)> = answer
        .kept
        .iter()
        .map(|kept| (title_of(app, &kept.id), session_reason(&kept.reason)))
        .collect();
    rows.extend(kept_row(&sessions, ("session", "sessions")));
    rows.extend(kept_text(
        &answer.kept_worktrees,
        &[],
        ("worktree", "worktrees"),
    ));
    if !pending.unselected.is_empty() {
        let count = plural(pending.unselected.len(), "session", "sessions");
        let names = pending.unselected.join(", ");
        rows.push(format!("Kept {count} you unselected: {names}."));
    }
    rows
}

fn session_reason(reason: &str) -> &str {
    match reason {
        "in_use" => "open in another app or working",
        "not_found" => "already gone",
        "error" => "could not be deleted",
        other => other,
    }
}

/// Drop the deleted sessions and removed worktrees from the snapshot the delete started from.
fn drop_deleted(app: &mut App, answer: &StorageSessionsDeleteResponse, measured_at: u64) {
    let removed: HashSet<&String> = answer.removed.iter().collect();
    let worktrees: HashSet<&String> = answer.removed_worktree_ids.iter().collect();
    apply_answer(app, measured_at, |snapshot| {
        let suggested = &mut snapshot.delete_suggestion;
        let gone: Vec<_> = snapshot
            .sessions
            .iter()
            .filter(|s| removed.contains(&s.session_id))
            .filter(|s| suggested.session_ids.contains(&s.session_id))
            .collect();
        let gone_bytes: u64 = gone.iter().filter_map(|s| s.delete_bytes).sum();
        suggested.reclaimable_bytes = suggested.reclaimable_bytes.saturating_sub(gone_bytes);
        suggested.session_ids.retain(|id| !removed.contains(id));
        let archive = &mut snapshot.archive_suggestion;
        archive.session_ids.retain(|id| !removed.contains(id));
        snapshot
            .sessions
            .retain(|s| !removed.contains(&s.session_id));
        snapshot.worktrees.retain(|w| !worktrees.contains(&w.id));
        for worktree in &mut snapshot.worktrees {
            worktree.session_ids.retain(|id| !removed.contains(id));
        }
    });
}
