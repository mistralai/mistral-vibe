//! The rows a bulk archive writes in the conversation, and its telemetry.

use std::collections::HashSet;

use super::actions::{plural, status, Outcome};
use super::bulk::Bulk;
use super::model::{self, Estimate};
use super::report;
use crate::app::App;
use crate::server::StorageUsageUpdated;
use crate::utils::bytes;

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
    let done: HashSet<String> = bulk.done.iter().cloned().collect();
    let refused = bulk
        .acted
        .iter()
        .filter(|a| matches!(a.outcome, Outcome::Busy))
        .count();
    archived(app, &bulk, &done, refused, measured);
    kept(app, &bulk);
}

fn archived(
    app: &mut App,
    bulk: &Bulk,
    done: &HashSet<String>,
    refused: usize,
    measured: Option<&StorageUsageUpdated>,
) {
    let freed = model::estimate(&bulk.start, done);
    let measured_freed = measured
        .and_then(|update| update.usage.snapshot.as_ref())
        .map(|after| model::measured_freed(&bulk.start, after, done));
    let complete = measured.is_some_and(|update| update.complete);
    report::bulk_archive(
        app,
        &report::BulkCounts {
            suggested: bulk.suggested,
            selected: bulk.selected.len(),
            archived: bulk.done.len(),
            refused,
            estimated: bulk.estimate.bytes,
            measured: measured_freed,
            complete,
        },
    );
    super::close(app);
    if bulk.done.is_empty() {
        return;
    }
    let count = plural(bulk.done.len(), "session", "sessions");
    status(app, &match freed_estimate(measured_freed.filter(|_| complete), freed) {
        Estimate { bytes: 0, at_least: false } => format!("Archived {count}. Their history is kept."),
        Estimate { bytes, at_least } => format!(
            "Archived {count}. {} {} is freed as their worktrees are removed; uncommitted work is saved to a git ref.",
            if at_least { "At least" } else { "About" },
            bytes::decimal(bytes)
        ),
    });
}

/// The refused, failed and unselected sessions.
fn kept(app: &mut App, bulk: &Bulk) {
    let title = |id: &String| {
        bulk.titles
            .iter()
            .find(|(t, _)| t == id)
            .map(|(_, t)| t.clone())
            .unwrap_or_default()
    };
    let busy: Vec<String> = bulk
        .acted
        .iter()
        .filter(|a| matches!(a.outcome, Outcome::Busy))
        .map(|a| title(&a.session_id))
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
    for acted in &bulk.acted {
        if let Outcome::Failed(error) = &acted.outcome {
            let title = title(&acted.session_id);
            crate::notices::failed(app, &format!("Failed to archive \"{title}\": {error}"));
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
