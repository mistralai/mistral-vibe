//! Enter in a review: the archive runs one session at a time, then waits for the scan that measures
//! it; the delete and cleanup reviews ask y/n first.

use std::collections::HashSet;
use std::sync::Arc;

use super::actions::{plural, title_of, Acted, Outcome};
use super::bulk_result::finish_bulk;
use super::model::{self, Estimate};
use super::{request, warn, ReviewKind};
use crate::app::App;
use crate::server::{Client, StorageRefreshParams, StorageSnapshot, StorageUsageUpdated};

pub use super::bulk_result::freed_estimate;

/// A bulk archive in flight, then waiting for the scan that measures it.
pub(super) struct Bulk {
    pub(super) suggested: usize,
    pub(super) selected: Vec<String>,
    pub(super) titles: Vec<(String, String)>,
    pub(super) estimate: Estimate,
    /// The numbers the bulk started from: later scans can replace the panel's.
    pub(super) start: StorageSnapshot,
    /// The sessions archived.
    pub(super) done: Vec<String>,
    pub(super) acted: Vec<Acted>,
    /// When the last archive answered: an older server's scan measures them if it started later.
    sent_at: u64,
    /// The measuring refresh's answer: `Some(None)` from a server without scan ids.
    scan_id: Option<Option<u64>>,
    /// Results that came with a scan id before the answer named one: the answer can still name them.
    early: Vec<StorageUsageUpdated>,
}

/// Enter in a review: the archive review archives its selection one session at a time; the delete
/// and cleanup reviews open their y/n question instead.
pub(super) fn start_bulk(app: &mut App, client: &Arc<Client>) {
    if app.storage.review.kind != ReviewKind::Archive {
        return super::actions::ask_review(app);
    }
    let review = &app.storage.review;
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let selected: Vec<String> = review
        .order
        .iter()
        .filter(|id| review.selected.contains(*id))
        .cloned()
        .collect();
    if selected.is_empty() {
        return warn(app, "Select at least one session to archive.");
    }
    let titles = review
        .order
        .iter()
        .map(|id| (id.clone(), title_of(app, id)))
        .collect();
    let chosen: HashSet<String> = selected.iter().cloned().collect();
    let bulk = Bulk {
        suggested: review.order.len(),
        estimate: model::estimate(snapshot, &chosen),
        start: snapshot.clone(),
        selected: selected.clone(),
        titles,
        done: Vec::new(),
        acted: Vec::new(),
        sent_at: 0,
        scan_id: None,
        early: Vec::new(),
    };
    let count = plural(selected.len(), "session", "sessions");
    app.storage.busy = Some(format!("Archiving {count}…"));
    app.storage.bulk = Some(bulk);
    request::bulk(app, client, selected);
}

/// Every archive answered: wait for the scan that measures them, unless none was archived.
pub(super) fn bulk_done(app: &mut App, client: &Arc<Client>, acted: Vec<Acted>) {
    let Some(bulk) = app.storage.bulk.as_mut() else {
        return;
    };
    bulk.done = acted
        .iter()
        .filter(|a| matches!(a.outcome, Outcome::Done))
        .map(|a| a.session_id.clone())
        .collect();
    bulk.acted = acted;
    bulk.sent_at = crate::utils::now_ms();
    if bulk.done.is_empty() {
        return finish_bulk(app, None);
    }
    let params = StorageRefreshParams {
        wait_for_session_ids: bulk.done.clone(),
        ..Default::default()
    };
    let count = plural(bulk.done.len(), "session", "sessions");
    app.storage.busy = Some(format!("Archived {count}. Measuring the space freed…"));
    request::refresh(app, client, params, true);
}

/// The measuring refresh answered with its scan; a failed request ends the wait without a measure.
pub(super) fn scan_answered(app: &mut App, answer: Result<Option<u64>, ()>) {
    let Some(bulk) = app.storage.bulk.as_mut().filter(|b| !b.done.is_empty()) else {
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
    let Some(bulk) = app.storage.bulk.as_mut().filter(|b| !b.done.is_empty()) else {
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
        .is_some_and(|bulk| !bulk.done.is_empty())
}

/// Esc while the scan measures a bulk archive: write the result without the measure.
pub(super) fn stop_waiting(app: &mut App) {
    if waiting_for_scan(app) {
        finish_bulk(app, None);
    }
}
