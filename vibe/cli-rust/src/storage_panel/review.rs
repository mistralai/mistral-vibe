//! What each review offers, in which order, and what its selection frees.

use std::collections::HashSet;

use super::model::{self, Estimate, Row};
use super::{leftovers, ReviewKind};
use crate::server::{StorageSnapshot, StorageSuggestion};

/// The server's session suggestion behind a review; the cleanup offers worktrees and folders instead.
pub fn suggestion(snapshot: &StorageSnapshot, kind: ReviewKind) -> Option<&StorageSuggestion> {
    match kind {
        ReviewKind::Archive => Some(&snapshot.archive_suggestion),
        ReviewKind::Delete => Some(&snapshot.delete_suggestion),
        ReviewKind::Cleanup => None,
    }
}

/// The review has something to offer here: a delete needs a suggested session this client can delete.
pub fn offered(snapshot: &StorageSnapshot, kind: ReviewKind, current: Option<&str>) -> bool {
    match kind {
        ReviewKind::Archive => !snapshot.archive_suggestion.session_ids.is_empty(),
        ReviewKind::Delete => !review_order(snapshot, kind, current, false).is_empty(),
        ReviewKind::Cleanup => leftovers::offered(snapshot),
    }
}

/// The offered ids still listed: sessions by size or oldest activity first (a delete leaves out open
/// ones), leftover worktrees then folders as the server lists them.
pub fn review_order(
    snapshot: &StorageSnapshot,
    kind: ReviewKind,
    current: Option<&str>,
    by_date: bool,
) -> Vec<String> {
    let Some(suggestion) = suggestion(snapshot, kind) else {
        let items = leftovers::items(snapshot).into_iter();
        return items.map(|item| item.id).collect();
    };
    let suggested: HashSet<&str> = suggestion.session_ids.iter().map(String::as_str).collect();
    let mut rows: Vec<Row> = model::rows(snapshot, current)
        .into_iter()
        .filter(|row| suggested.contains(row.session.session_id.as_str()))
        .filter(|row| kind == ReviewKind::Archive || !row.locked())
        .collect();
    if by_date {
        rows.sort_by_key(|row| row.session.last_activity_at);
    }
    rows.into_iter()
        .map(|row| row.session.session_id.clone())
        .collect()
}

/// What a review's selection frees; `None` when unknown.
pub fn review_estimate(
    snapshot: &StorageSnapshot,
    kind: ReviewKind,
    selected: &HashSet<String>,
) -> Option<Estimate> {
    match kind {
        ReviewKind::Archive => Some(model::estimate(snapshot, selected)),
        ReviewKind::Delete => super::delete::estimate(snapshot, selected),
        ReviewKind::Cleanup => Some(leftovers::estimate(snapshot, selected)),
    }
}

/// What the suggestion row says it frees: the server's total and whether it is a floor.
pub fn suggested_estimate(snapshot: &StorageSnapshot, kind: ReviewKind) -> Estimate {
    match suggestion(snapshot, kind) {
        Some(suggestion) => Estimate {
            bytes: suggestion.reclaimable_bytes,
            at_least: suggestion.reclaimable_at_least,
        },
        None => {
            let offered = review_order(snapshot, kind, None, false)
                .into_iter()
                .collect();
            Estimate {
                bytes: snapshot.leftover_cleanup.reclaimable_bytes,
                at_least: leftovers::estimate(snapshot, &offered).at_least,
            }
        }
    }
}

/// The ids a new snapshot still offers the open review, in the review's order.
pub fn still_offered(
    snapshot: &StorageSnapshot,
    kind: ReviewKind,
    order: &[String],
) -> Vec<String> {
    let offered: HashSet<String> = match suggestion(snapshot, kind) {
        Some(suggestion) => suggestion.session_ids.iter().cloned().collect(),
        None => leftovers::items(snapshot)
            .into_iter()
            .map(|i| i.id)
            .collect(),
    };
    order
        .iter()
        .filter(|id| offered.contains(*id))
        .cloned()
        .collect()
}
