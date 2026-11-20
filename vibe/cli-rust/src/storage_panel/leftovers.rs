//! Leftover cleanup: the worktrees and folders offered, their sizes, the clean request and its result rows.

use std::collections::HashSet;
use std::sync::Arc;

use super::actions::{plural, status};
use super::model::{space, Estimate, Space};
use super::outcome::{apply_answer, kept_row};
use super::{report, request, warn, View};
use crate::app::App;
use crate::server::{
    Client, StorageLeftoverKept, StorageLeftoversCleanParams, StorageLeftoversCleanResponse,
    StorageSnapshot,
};

/// One offered leftover: an unclaimed worktree or a folder with no claim and no `.git`.
#[derive(Clone, Debug)]
pub struct Item {
    pub id: String,
    pub worktree: bool,
    /// The repository name; empty for a folder.
    pub name: String,
    pub branch: Option<String>,
    pub path: String,
    /// `None`: a worktree not measured yet.
    pub bytes: Option<u64>,
}

/// A clean request in flight: what it started from, for the result row and telemetry.
pub(super) struct Pending {
    offered: usize,
    selected: usize,
    estimate: Estimate,
    items: Vec<Item>,
    /// When the snapshot the cleanup started from was measured: a newer one already shows the removal.
    measured_at: u64,
}

/// Every offered leftover, worktrees first, sized from the same snapshot.
pub fn items(snapshot: &StorageSnapshot) -> Vec<Item> {
    let cleanup = &snapshot.leftover_cleanup;
    let worktrees = cleanup.worktree_ids.iter().map(|id| {
        let worktree = snapshot.worktrees.iter().find(|w| w.id == *id);
        Item {
            id: id.clone(),
            worktree: true,
            name: worktree
                .and_then(|w| w.repo_name.clone())
                .unwrap_or_default(),
            branch: worktree.and_then(|w| w.branch.clone()),
            path: worktree.map_or_else(|| id.clone(), |w| w.path.clone()),
            bytes: worktree.and_then(|w| w.bytes),
        }
    });
    let folders = cleanup.unknown_folders.iter().map(|folder| Item {
        id: folder.id.clone(),
        worktree: false,
        name: String::new(),
        branch: None,
        path: folder.path.clone(),
        bytes: Some(folder.bytes),
    });
    worktrees.chain(folders).collect()
}

pub fn offered(snapshot: &StorageSnapshot) -> bool {
    let cleanup = &snapshot.leftover_cleanup;
    !cleanup.worktree_ids.is_empty() || !cleanup.unknown_folders.is_empty()
}

/// The selected items' sizes; `at_least` when a selected worktree is not measured yet.
pub fn estimate(snapshot: &StorageSnapshot, selected: &HashSet<String>) -> Estimate {
    sum(items(snapshot).iter().filter(|i| selected.contains(&i.id)))
}

fn sum<'a>(items: impl Iterator<Item = &'a Item>) -> Estimate {
    items.fold(Estimate::default(), |estimate, item| Estimate {
        bytes: estimate.bytes + item.bytes.unwrap_or(0),
        at_least: estimate.at_least || item.bytes.is_none(),
    })
}

/// `Clean up 2 leftover worktrees and 1 folder`.
pub fn suggestion_text(snapshot: &StorageSnapshot) -> String {
    let cleanup = &snapshot.leftover_cleanup;
    let worktrees = cleanup.worktree_ids.len();
    let folders = cleanup.unknown_folders.len();
    let what = match (worktrees, folders) {
        (w, 0) => plural(w, "leftover worktree", "leftover worktrees"),
        (0, f) => plural(f, "leftover folder", "leftover folders"),
        (w, f) => format!(
            "{} and {}",
            plural(w, "leftover worktree", "leftover worktrees"),
            plural(f, "folder", "folders")
        ),
    };
    format!("Clean up {what}")
}

/// Enter in the cleanup review: one `storage/leftovers/clean` with the selected ids.
pub(super) fn start(app: &mut App, client: &Arc<Client>) {
    if let Some(params) = begin(app) {
        request::clean(app, client, params);
    }
}

/// Mark the cleanup in flight and build its request; `None` when nothing is selected.
pub fn begin(app: &mut App) -> Option<StorageLeftoversCleanParams> {
    let snapshot = app.storage.usage.snapshot.as_ref()?;
    let measured_at = snapshot.measured_at;
    let review = &app.storage.review;
    let items: Vec<Item> = items(snapshot)
        .into_iter()
        .filter(|item| review.order.contains(&item.id))
        .collect();
    let chosen: Vec<&Item> = items
        .iter()
        .filter(|item| review.selected.contains(&item.id))
        .collect();
    if chosen.is_empty() {
        warn(app, "Select at least one item to clean up.");
        return None;
    }
    let ids = |worktree: bool| -> Vec<String> {
        let mine = chosen.iter().filter(|item| item.worktree == worktree);
        mine.map(|item| item.id.clone()).collect()
    };
    let params = StorageLeftoversCleanParams {
        worktree_ids: ids(true),
        folder_ids: ids(false),
    };
    let count = plural(chosen.len(), "item", "items");
    app.storage.cleanup = Some(Pending {
        offered: review.order.len(),
        selected: chosen.len(),
        estimate: sum(chosen.into_iter()),
        items,
        measured_at,
    });
    app.storage.busy = Some(format!("Cleaning up {count}…"));
    Some(params)
}

/// The server answered: write the result rows, drop the removed items, and wait for the refresh it started.
pub fn cleaned(app: &mut App, answer: Result<StorageLeftoversCleanResponse, String>) {
    app.storage.busy = None;
    let Some(pending) = app.storage.cleanup.take() else {
        return;
    };
    app.storage.view = View::Panel;
    let answer = answer.unwrap_or_else(|error| {
        crate::notices::failed(app, &format!("Failed to clean up leftovers: {error}"));
        StorageLeftoversCleanResponse::default()
    });
    report::leftover_cleanup(
        app,
        &report::CleanupCounts {
            offered: pending.offered,
            selected: pending.selected,
            removed: answer.removed.len(),
            kept: answer.kept.len(),
            estimated: pending.estimate.bytes,
        },
    );
    let removed: HashSet<&String> = answer.removed.iter().collect();
    let freed = sum(pending.items.iter().filter(|i| removed.contains(&i.id)));
    if !removed.is_empty() {
        let items = plural(removed.len(), "item", "items");
        let freed = space(Space::Freed, Some(freed));
        status(app, &format!("Cleaned up {items}. {freed}"));
    }
    if let Some(text) = kept_text(&answer.kept, &pending.items, ("item", "items")) {
        status(app, &text);
    }
    drop_removed(app, &removed, pending.measured_at);
}

/// `Kept 2 items: in use (target), already gone (old-spike).`, naming each kept item by its folder.
pub fn kept_text(
    kept: &[StorageLeftoverKept],
    items: &[Item],
    noun: (&str, &str),
) -> Option<String> {
    let named: Vec<(String, &str)> = kept
        .iter()
        .map(|kept| {
            let item = items.iter().find(|item| item.id == kept.id);
            let path = item.map_or(kept.id.as_str(), |item| item.path.as_str());
            let name = path.rsplit(['/', '\\']).find(|p| !p.is_empty());
            (name.unwrap_or(path).to_owned(), reason(&kept.reason))
        })
        .collect();
    kept_row(&named, noun)
}

fn reason(reason: &str) -> &str {
    match reason {
        "claimed" => "a session uses it again",
        "in_use" => "in use",
        "kept_dirty" => "its uncommitted work could not be saved",
        "not_found" => "already gone",
        "unsafe" => "not safe to remove",
        "error" => "could not be removed",
        "too_large_to_check" => "too large to check that nothing uses it",
        other => other,
    }
}

/// Drop the removed items from the snapshot the cleanup started from.
fn drop_removed(app: &mut App, removed: &HashSet<&String>, measured_at: u64) {
    apply_answer(app, measured_at, |snapshot| {
        let cleanup = &mut snapshot.leftover_cleanup;
        cleanup.worktree_ids.retain(|id| !removed.contains(id));
        cleanup.unknown_folders.retain(|f| !removed.contains(&f.id));
        snapshot.worktrees.retain(|w| !removed.contains(&w.id));
        snapshot.leftover_cleanup.reclaimable_bytes = sum(items(snapshot).iter()).bytes;
    });
}
