//! `/storage`: Vibe's disk usage, the archive review, single archive and delete.

pub mod actions;
pub mod bulk;
mod keys;
pub mod model;
mod report;
mod request;
pub mod startup;

use std::collections::HashSet;
use std::sync::Arc;

use serde_json::Value;

use crate::app::{App, Severity};
use crate::option_list::{MultiAction, OptionList};
use crate::server::{Client, StorageRefreshParams, StorageUsage, StorageUsageUpdated};

pub use bulk::waiting_for_scan;
pub use keys::handle_key;

/// Numbers older than this refresh when the panel opens.
const MAX_AGE_MS: u64 = 10 * 60 * 1_000;
pub const UNSUPPORTED: &str = "Storage usage needs a newer Vibe app server.";

#[derive(Default)]
pub struct State {
    /// The `vibe_storage_usage` flag, from the runtime config: off hides `/storage` and the low-disk notice.
    pub enabled: bool,
    /// While set the panel replaces the input box.
    pub open: bool,
    /// A `/storage` read is in flight.
    loading: bool,
    pub usage: StorageUsage,
    pub view: View,
    /// Panel options: the suggestion when there is one, then every session.
    pub list: OptionList<MultiAction>,
    pub review: Review,
    pub confirm: Option<Confirm>,
    /// What runs now; keys wait until it answers.
    pub busy: Option<String>,
    /// A bulk archive waiting for the scan that measures it.
    bulk: Option<bulk::Bulk>,
    /// A scan runs: the numbers shown are about to be replaced.
    pub refreshing: bool,
    /// The newest scan whose result arrived: a late refresh answer naming it or an older one is ignored.
    settled: Option<u64>,
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
pub enum View {
    #[default]
    Panel,
    Details,
    Review,
}

/// The bulk archive review: suggested sessions, the selection and the sort.
#[derive(Default)]
pub struct Review {
    pub order: Vec<String>,
    pub selected: HashSet<String>,
    pub by_date: bool,
    pub list: OptionList<MultiAction>,
}

/// A single-row action waiting for `y`.
pub struct Confirm {
    pub action: Action,
    pub session_id: String,
    pub title: String,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Archive,
    Unarchive,
    Delete,
}

/// The highlighted panel option.
pub enum Target<'a> {
    Suggestion,
    Session(model::Row<'a>),
}

pub enum Event {
    Read(Result<StorageUsage, String>),
    Startup(Option<startup::Notice>),
    Acted(actions::Acted),
    Bulk(Vec<(String, actions::Outcome)>),
    /// A refresh answered with the scan whose `storage/usage/updated` will answer it.
    Refreshed {
        bulk: bool,
        answer: Result<Option<u64>, ()>,
        /// The server started or joined a scan rather than finding the numbers fresh.
        scanning: bool,
    },
}

pub fn open(app: &mut App, client: &Arc<Client>) {
    if app.storage.open || app.storage.loading {
        return;
    }
    app.storage.loading = true;
    request::read(app, client);
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Read(result) => {
            app.storage.loading = false;
            match result {
                Ok(usage) => show(app, client, usage),
                Err(error) if error == UNSUPPORTED => crate::notices::refused(app, UNSUPPORTED),
                Err(error) => {
                    crate::notices::failed(app, &format!("Failed to read storage usage: {error}"))
                }
            }
        }
        Event::Startup(notice) => startup::apply(app, notice),
        Event::Acted(acted) => actions::acted(app, client, acted),
        Event::Bulk(outcomes) => bulk::bulk_done(app, client, outcomes),
        Event::Refreshed {
            bulk,
            answer,
            scanning,
        } => {
            let scan = answer.as_ref().ok().copied().flatten();
            if scanning && still_running(scan, app.storage.settled) {
                app.storage.refreshing = true;
            }
            if bulk {
                bulk::scan_answered(app, answer);
            }
        }
    }
}

fn show(app: &mut App, client: &Arc<Client>, usage: StorageUsage) {
    report::opened(app, &usage);
    app.storage = State {
        enabled: app.storage.enabled,
        open: true,
        refreshing: usage.refreshing(),
        usage,
        ..State::default()
    };
    refresh(
        app,
        client,
        StorageRefreshParams {
            max_age_ms: Some(MAX_AGE_MS),
            ..Default::default()
        },
    );
}

pub fn close(app: &mut App) {
    app.storage.open = false;
    app.storage.confirm = None;
}

pub(crate) fn refresh(app: &App, client: &Arc<Client>, params: StorageRefreshParams) {
    request::refresh(app, client, params, false);
}

/// Whether a refresh answer's scan may still run: ids increase per app-server process, so one at or before `settled` is done.
pub fn still_running(scan: Option<u64>, settled: Option<u64>) -> bool {
    match (scan, settled) {
        (Some(scan), Some(settled)) => scan > settled,
        _ => true,
    }
}

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
pub(crate) fn replace(app: &mut App, usage: StorageUsage) {
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
    let offset = usize::from(has_suggestion(&app.storage.usage));
    let index = kept
        .and_then(|id| rows.iter().position(|row| row.session.session_id == id))
        .map_or(app.storage.list.selected(), |index| index + offset);
    let len = rows.len() + offset;
    app.storage.list.highlight(index.min(len.saturating_sub(1)));
    if app.storage.view == View::Details && !matches!(target(app), Some(Target::Session(_))) {
        app.storage.view = View::Panel;
    }
}

pub fn has_suggestion(usage: &StorageUsage) -> bool {
    usage
        .snapshot
        .as_ref()
        .is_some_and(|s| !s.archive_suggestion.session_ids.is_empty())
}

/// The highlighted panel option, if any.
pub fn target(app: &App) -> Option<Target<'_>> {
    let snapshot = app.storage.usage.snapshot.as_ref()?;
    let mut index = app.storage.list.selected();
    if has_suggestion(&app.storage.usage) {
        if index == 0 {
            return Some(Target::Suggestion);
        }
        index -= 1;
    }
    let rows = model::rows(snapshot, app.session.session_id.as_deref());
    rows.get(index).copied().map(Target::Session)
}

/// A refusal shown on the inline notice, the panel staying open.
fn warn(app: &mut App, message: &str) {
    crate::notices::inline(app, Severity::Warning, message);
}
