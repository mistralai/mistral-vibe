//! `/storage`: Vibe's disk usage, the archive, delete and cleanup reviews, single archive and delete.

pub mod actions;
pub mod bulk;
mod bulk_result;
pub mod delete;
mod keys;
pub mod leftovers;
pub mod model;
pub mod outcome;
mod refresh;
mod report;
mod request;
pub mod review;
pub mod startup;

use std::collections::HashSet;
use std::sync::Arc;

use crate::app::{App, Severity};
use crate::option_list::{MultiAction, OptionList};
use crate::server::{Client, StorageRefreshParams, StorageUsage};

pub use bulk::waiting_for_scan;
pub use keys::handle_key;
pub use refresh::on_updated;
pub use refresh::replace;

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
    /// Panel options: the suggestions there are, then every session.
    pub list: OptionList<MultiAction>,
    pub review: Review,
    pub confirm: Option<Confirm>,
    /// What runs now; keys wait until it answers.
    pub busy: Option<String>,
    /// A bulk archive in flight, or waiting for the scan that measures it.
    bulk: Option<bulk::Bulk>,
    /// A `storage/sessions/delete` waiting for the server's answer.
    deleting: Option<delete::Pending>,
    /// A leftover cleanup waiting for the server's answer.
    cleanup: Option<leftovers::Pending>,
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

/// A bulk review: suggested sessions, the selection and the sort.
#[derive(Default)]
pub struct Review {
    pub kind: ReviewKind,
    pub order: Vec<String>,
    pub selected: HashSet<String>,
    pub by_date: bool,
    pub list: OptionList<MultiAction>,
}

/// An action waiting for `y`: a row's, or a delete or cleanup review's.
pub enum Confirm {
    Row {
        action: Action,
        session_id: String,
        title: String,
        /// What a delete frees: the row's `deleteBytes`; `None` when unknown.
        frees: Option<model::Estimate>,
    },
    Review {
        kind: ReviewKind,
        count: usize,
        frees: Option<model::Estimate>,
    },
}

impl Confirm {
    /// Removes something for good: only `y` confirms, so a second Enter never does.
    pub fn destructive(&self) -> bool {
        !matches!(
            self,
            Confirm::Row {
                action: Action::Archive | Action::Unarchive,
                ..
            }
        )
    }
}

/// What a review does to its selection.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum ReviewKind {
    /// Active sessions idle 30+ days.
    #[default]
    Archive,
    /// Archived sessions idle 30+ days.
    Delete,
    /// Worktrees no session claims and unknown folders of the worktree buckets.
    Cleanup,
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Archive,
    Unarchive,
    Delete,
}

/// The highlighted panel option.
pub enum Target<'a> {
    Suggestion(ReviewKind),
    Session(model::Row<'a>),
}

pub enum Event {
    Read(Result<Box<StorageUsage>, String>),
    Startup(Option<startup::Notice>),
    Acted(actions::Acted),
    Bulk(Vec<actions::Acted>),
    Cleaned(Result<crate::server::StorageLeftoversCleanResponse, String>),
    Deleted(Result<crate::server::StorageSessionsDeleteResponse, String>),
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
                Ok(usage) => show(app, client, *usage),
                Err(error) if error == UNSUPPORTED => crate::notices::refused(app, UNSUPPORTED),
                Err(error) => {
                    crate::notices::failed(app, &format!("Failed to read storage usage: {error}"))
                }
            }
        }
        Event::Startup(notice) => startup::apply(app, notice),
        Event::Acted(acted) => actions::acted(app, client, acted),
        Event::Bulk(acted) => bulk::bulk_done(app, client, acted),
        Event::Cleaned(answer) => leftovers::cleaned(app, answer),
        Event::Deleted(answer) => delete::deleted(app, answer),
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

/// The suggestion rows the panel lists first: archive, delete, then cleanup.
pub fn suggestions(app: &App) -> Vec<ReviewKind> {
    let Some(snapshot) = app.storage.usage.snapshot.as_ref() else {
        return Vec::new();
    };
    let current = app.session.session_id.as_deref();
    [ReviewKind::Archive, ReviewKind::Delete, ReviewKind::Cleanup]
        .into_iter()
        .filter(|kind| review::offered(snapshot, *kind, current))
        .collect()
}

/// The highlighted panel option, if any.
pub fn target(app: &App) -> Option<Target<'_>> {
    let snapshot = app.storage.usage.snapshot.as_ref()?;
    let index = app.storage.list.selected();
    let suggestions = suggestions(app);
    if let Some(kind) = suggestions.get(index) {
        return Some(Target::Suggestion(*kind));
    }
    let rows = model::rows(snapshot, app.session.session_id.as_deref());
    rows.into_iter()
        .nth(index - suggestions.len())
        .map(Target::Session)
}

/// A refusal shown on the inline notice, the panel staying open.
fn warn(app: &mut App, message: &str) {
    crate::notices::inline(app, Severity::Warning, message);
}
