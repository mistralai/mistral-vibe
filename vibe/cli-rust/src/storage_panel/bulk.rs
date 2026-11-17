//! The bulk archive: one session at a time, then the scan that measures it, then the result rows.

use std::collections::HashSet;
use std::sync::Arc;

use super::actions::{plural, status, title_of, Outcome};
use super::model::{self, Estimate};
use super::{report, request, warn};
use crate::app::App;
use crate::server::{Client, StorageRefreshParams, StorageSnapshot, StorageUsageUpdated};
use crate::utils::bytes;

/// A bulk archive in flight, then waiting for the scan that measures it.
pub(super) struct Bulk {
    suggested: usize,
    selected: Vec<String>,
    titles: Vec<(String, String)>,
    estimate: Estimate,
    /// The numbers the archive started from: later scans can replace the panel's.
    start: StorageSnapshot,
    archived: Vec<String>,
    outcomes: Vec<(String, Outcome)>,
    /// When the last archive answered: an older server's scan measures them if it started later.
    sent_at: u64,
    /// The measuring refresh's answer: `Some(None)` from a server without scan ids.
    scan_id: Option<Option<u64>>,
    /// Results that came with a scan id before the answer named one: the answer can still name them.
    early: Vec<StorageUsageUpdated>,
}

/// Enter in the review: archive the selection, one session at a time.
pub(super) fn start_bulk(app: &mut App, client: &Arc<Client>) {
    let review = &app.storage.review;
    let selected: Vec<String> = review
        .order
        .iter()
        .filter(|id| review.selected.contains(*id))
        .cloned()
        .collect();
    if selected.is_empty() {
        return warn(app, "Select at least one session to archive.");
    }
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let titles = review
        .order
        .iter()
        .map(|id| (id.clone(), title_of(app, id)))
        .collect();
    let bulk = Bulk {
        suggested: review.order.len(),
        estimate: model::estimate(snapshot, &review.selected),
        start: snapshot.clone(),
        selected: selected.clone(),
        titles,
        archived: Vec::new(),
        outcomes: Vec::new(),
        sent_at: 0,
        scan_id: None,
        early: Vec::new(),
    };
    app.storage.busy = Some(format!(
        "Archiving {}…",
        plural(selected.len(), "session", "sessions")
    ));
    app.storage.bulk = Some(bulk);
    request::bulk(app, client, selected);
}

/// Every archive answered: wait for the scan that measures them, unless none was archived.
pub(super) fn bulk_done(app: &mut App, client: &Arc<Client>, outcomes: Vec<(String, Outcome)>) {
    let Some(bulk) = app.storage.bulk.as_mut() else {
        return;
    };
    bulk.archived = outcomes
        .iter()
        .filter(|(_, o)| matches!(o, Outcome::Done))
        .map(|(id, _)| id.clone())
        .collect();
    bulk.outcomes = outcomes;
    bulk.sent_at = crate::utils::now_ms();
    if bulk.archived.is_empty() {
        return finish_bulk(app, None);
    }
    let count = plural(bulk.archived.len(), "session", "sessions");
    let wait_for_session_ids = bulk.archived.clone();
    app.storage.busy = Some(format!("Archived {count}. Measuring the space freed…"));
    let params = StorageRefreshParams {
        wait_for_session_ids,
        ..Default::default()
    };
    request::refresh(app, client, params, true);
}

/// The measuring refresh answered with its scan; a failed request ends the wait without a measure.
pub(super) fn scan_answered(app: &mut App, answer: Result<Option<u64>, ()>) {
    let Some(bulk) = app.storage.bulk.as_mut().filter(|b| !b.archived.is_empty()) else {
        return;
    };
    let Ok(scan_id) = answer else {
        return finish_bulk(app, None);
    };
    bulk.scan_id = Some(scan_id);
    let early = std::mem::take(&mut bulk.early);
    let named = early.into_iter().find(|update| update.scan_id == scan_id);
    if let Some(update) = named {
        settle(app, &update);
    }
}

/// A result of the scan the refresh named closes the panel, without a measure when it failed.
/// Before the answer, a result with a scan id waits for it; a server without ids is matched by
/// its first success that started after the archives.
pub(super) fn bulk_measured(app: &mut App, update: &StorageUsageUpdated) -> bool {
    let Some(bulk) = app.storage.bulk.as_mut().filter(|b| !b.archived.is_empty()) else {
        return false;
    };
    let ours = match matches(bulk.scan_id, bulk.sent_at, update) {
        Match::Early(_) => {
            bulk.early.push(update.clone());
            return false;
        }
        Match::Ours => true,
        Match::Other => false,
    };
    if ours {
        settle(app, update);
    }
    ours
}

/// How one `storage/usage/updated` relates to the scan a bulk archive waits for.
#[derive(Debug, PartialEq, Eq)]
pub enum Match {
    Ours,
    Other,
    /// Tagged with a scan id before the refresh answered: kept until the answer names one.
    Early(u64),
}

/// `answer` is the refresh's answer (`Some(None)` from a server without scan ids), `sent_at`
/// when the last archive answered. A scan's `refreshing` start is never its result.
pub fn matches(answer: Option<Option<u64>>, sent_at: u64, update: &StorageUsageUpdated) -> Match {
    let usage = &update.usage;
    if usage.refreshing() {
        return Match::Other;
    }
    match (answer, update.scan_id) {
        (None, Some(got)) => Match::Early(got),
        (Some(Some(wanted)), Some(got)) if wanted == got => Match::Ours,
        (Some(Some(_)), Some(_)) => Match::Other,
        (Some(Some(_)), None) => Match::Other,
        // No scan ids: a failed scan reports the cached snapshot, so any failure ends the wait.
        _ if usage.status == "failed" => Match::Ours,
        _ if usage
            .snapshot
            .as_ref()
            .is_some_and(|s| s.scan_started_at >= sent_at) =>
        {
            Match::Ours
        }
        _ => Match::Other,
    }
}

fn settle(app: &mut App, update: &StorageUsageUpdated) {
    finish_bulk(app, Some(update).filter(|u| u.usage.status != "failed"));
}

/// The archives are done and the scan that measures them runs; Esc stops waiting.
pub fn waiting_for_scan(app: &App) -> bool {
    app.storage
        .bulk
        .as_ref()
        .is_some_and(|bulk| !bulk.archived.is_empty())
}

/// Esc while the scan measures a bulk archive: write the result without the measure.
pub(super) fn stop_waiting(app: &mut App) {
    if waiting_for_scan(app) {
        finish_bulk(app, None);
    }
}

/// The result row's space: the measured drop once the scan saw every worktree go, else the
/// estimate on the numbers the archive started from.
pub fn freed_estimate(measured: Option<u64>, estimate: Estimate) -> Estimate {
    match measured {
        Some(bytes) if bytes > 0 => Estimate {
            bytes,
            at_least: false,
        },
        _ => estimate,
    }
}

/// Close the panel and write the result rows; `measured` is `None` when the user did not wait.
pub(super) fn finish_bulk(app: &mut App, measured: Option<&StorageUsageUpdated>) {
    let Some(bulk) = app.storage.bulk.take() else {
        return;
    };
    let archived: HashSet<String> = bulk.archived.iter().cloned().collect();
    let freed = model::estimate(&bulk.start, &archived);
    let measured_freed = measured
        .and_then(|update| update.usage.snapshot.as_ref())
        .map(|after| model::measured_freed(&bulk.start, after, &archived));
    let complete = measured.is_some_and(|update| update.complete);
    report::bulk_archive(
        app,
        &report::BulkCounts {
            suggested: bulk.suggested,
            selected: bulk.selected.len(),
            archived: bulk.archived.len(),
            refused: bulk
                .outcomes
                .iter()
                .filter(|(_, o)| matches!(o, Outcome::Busy))
                .count(),
            estimated: bulk.estimate.bytes,
            measured: measured_freed,
            complete,
        },
    );
    super::close(app);
    let title = |id: &String| {
        bulk.titles
            .iter()
            .find(|(t, _)| t == id)
            .map(|(_, t)| t.clone())
            .unwrap_or_default()
    };
    if !bulk.archived.is_empty() {
        let count = plural(bulk.archived.len(), "session", "sessions");
        status(app, &match freed_estimate(measured_freed.filter(|_| complete), freed) {
            Estimate { bytes: 0, at_least: false } => format!("Archived {count}. Their history is kept."),
            Estimate { bytes, at_least } => format!(
                "Archived {count}. {} {} is freed as their worktrees are removed; uncommitted work is saved to a git ref.",
                if at_least { "At least" } else { "About" },
                bytes::decimal(bytes)
            ),
        });
    }
    let busy: Vec<String> = bulk
        .outcomes
        .iter()
        .filter(|(_, o)| matches!(o, Outcome::Busy))
        .map(|(id, _)| title(id))
        .collect();
    if !busy.is_empty() {
        status(
            app,
            &format!(
                "Kept {}, busy (working or open in another app): {}.",
                plural(busy.len(), "session", "sessions"),
                busy.join(", ")
            ),
        );
    }
    for (id, outcome) in &bulk.outcomes {
        if let Outcome::Failed(error) = outcome {
            crate::notices::failed(
                app,
                &format!("Failed to archive \"{}\": {error}", title(id)),
            );
        }
    }
    let unselected: Vec<String> = bulk
        .titles
        .iter()
        .filter(|(id, _)| !bulk.selected.contains(id))
        .map(|(_, t)| t.clone())
        .collect();
    if !unselected.is_empty() {
        let count = plural(unselected.len(), "session", "sessions");
        status(
            app,
            &format!("Kept {count} you unselected: {}.", unselected.join(", ")),
        );
    }
}
