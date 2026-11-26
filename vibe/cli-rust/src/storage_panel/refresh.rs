//! New numbers: a finished scan replaces the panel's, keeping the highlight and pruning an open review.

use serde_json::Value;

use super::{actions, bulk, model, review, suggestions, target, Confirm, ReviewKind, Target, View};
use crate::app::{App, Severity};
use crate::server::{StorageUsage, StorageUsageUpdated};

/// `storage/usage/updated`: a starting scan marks the numbers refreshing, a finished one replaces them.
pub fn on_updated(app: &mut App, params: &Value) {
    let update: StorageUsageUpdated = crate::utils::json::read(params);
    if !app.storage.open {
        return;
    }
    app.storage.refreshing = update.usage.refreshing();
    if update.usage.refreshing() {
        return;
    }
    app.storage.settled = update.scan_id.max(app.storage.settled);
    if bulk::bulk_measured(app, &update) {
        return;
    }
    replace(app, update.usage);
}

/// New numbers, keeping the highlighted session highlighted.
pub fn replace(app: &mut App, usage: StorageUsage) {
    let current = app.session.session_id.clone();
    let kept = match target(app) {
        Some(Target::Session(row)) => Some(row.session.session_id.clone()),
        _ => None,
    };
    app.storage.usage = usage;
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return;
    };
    let rows = model::rows(snapshot, current.as_deref());
    let offset = suggestions(app).len();
    let index = kept
        .and_then(|id| rows.iter().position(|row| row.session.session_id == id))
        .map_or(app.storage.list.selected(), |index| index + offset);
    let len = rows.len() + offset;
    app.storage.list.highlight(index.min(len.saturating_sub(1)));
    if app.storage.view == View::Details && !matches!(target(app), Some(Target::Session(_))) {
        app.storage.view = View::Panel;
    }
    prune_review(app);
}

/// Keep the open review to what the new snapshot still offers, so a stale id is never sent; with
/// nothing left the review closes with a note.
fn prune_review(app: &mut App) {
    let (Some(snapshot), View::Review) = (app.storage.usage.snapshot.as_ref(), app.storage.view)
    else {
        return;
    };
    let review = &mut app.storage.review;
    if review.kind == ReviewKind::Archive {
        return;
    }
    let order = review::still_offered(snapshot, review.kind, &review.order);
    if order.len() == review.order.len() {
        return;
    }
    let kept = review.order.get(review.list.selected()).cloned();
    review.selected.retain(|id| order.contains(id));
    review.order = order;
    let index = kept.and_then(|id| review.order.iter().position(|o| *o == id));
    review
        .list
        .select(index.unwrap_or(0).min(review.order.len().saturating_sub(1)));
    if review.order.is_empty() {
        app.storage.view = View::Panel;
        app.storage.confirm = None;
        crate::notices::inline(
            app,
            Severity::Information,
            "Nothing is left to review: the numbers changed.",
        );
    } else if matches!(app.storage.confirm, Some(Confirm::Review { .. })) {
        app.storage.confirm = None;
        actions::ask_review(app);
    }
}
