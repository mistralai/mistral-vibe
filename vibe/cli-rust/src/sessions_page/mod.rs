//! The Sessions page: every saved session, grouped by project, opened in one keystroke.

pub mod actions;
pub mod adopt;
mod answers;
pub mod browse;
pub mod folders;
pub mod group;
pub mod keys;
mod layout;
pub mod leases;
mod load;
pub mod model;
pub mod mouse;
pub mod preview;
pub mod preview_view;
pub mod rename;
mod requests;
pub mod row;
mod switch;
mod trigger;

use std::collections::HashSet;
use std::sync::Arc;

use crate::app::App;
use crate::server::{Client, PublicSessionState};

pub use adopt::{load_history, short_id};
pub use browse::{Browse, Column};
pub use layout::{highlighted_id, relayout, relayout_on, selected};
pub use leases::{held, lease_locked};
pub use model::Sort;
pub use preview::{read_due as read_preview, Preview};
pub use row::{Project, Session};
pub use trigger::left_opens;

/// Sessions the page loads at most; past it the oldest are left out.
pub const MAX_SESSIONS: usize = 5000;

/// What opened the page, for telemetry.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Trigger {
    #[default]
    Left,
    SessionsCommand,
    ResumeCommand,
    Startup,
}

impl Trigger {
    pub fn name(self) -> &'static str {
        match self {
            Self::Left => "left",
            Self::SessionsCommand => "sessions_command",
            Self::ResumeCommand => "resume_command",
            Self::Startup => "startup",
        }
    }
}

#[derive(Default)]
pub struct SessionsPage {
    pub open: bool,
    pub trigger: Trigger,
    pub loading: bool,
    /// A refresh under listed rows: its pages fill `incoming` instead.
    pub reloading: bool,
    /// More sessions exist than the page loaded.
    pub truncated: bool,
    pub sessions: Vec<Session>,
    /// A refresh's pages, swapped in whole once the last one lands.
    pub incoming: Vec<Session>,
    pub projects: Vec<Project>,
    pub view: model::View,
    /// Kept across opens for the whole process.
    pub browse: Browse,
    pub help: bool,
    /// The title editor `r` opens over the page.
    pub rename: Option<rename::Rename>,
    /// A request that owns the page until it answers (open, new).
    pub busy: Option<String>,
    /// Numbers each open and new: an answer for an older one is dropped.
    pub ticket: u64,
    pub preview: Preview,
    /// The terminal is wide enough for the folders column: the sessions column is then a flat list.
    pub folders_shown: bool,
    /// Where the folders column was painted, for the mouse.
    pub folders_area: ratatui::layout::Rect,
    /// The list request generation; a late page from an older open is dropped.
    pub generation: u64,
    /// Sessions whose lease another process holds.
    pub locked: HashSet<String>,
    pub lease_notified: HashSet<String>,
    pub lease_ended: bool,
    pub subscribed: bool,
    /// Opened before the app server was ready: the list loads once it is.
    pub awaiting_server: bool,
    /// A session switch the page handed to the conversation view, still loading.
    pub switching: Option<Switching>,
}

pub use switch::{switching, Switching};

pub enum Event {
    Page {
        generation: u64,
        sessions: Vec<Session>,
        next: Option<String>,
    },
    /// The `projects/list` answer for the load numbered `generation`.
    Projects {
        generation: u64,
        projects: Vec<Project>,
    },
    /// A `session/list` read failed, for the load numbered `generation`.
    Failed {
        generation: u64,
        message: String,
    },
    Leases(HashSet<String>),
    Preview(preview::Event),
    Action(actions::Event),
}

/// Open the page on the listing and place it was left on, refreshing it; the
/// current session stays attached underneath.
pub fn open(app: &mut App, client: &Arc<Client>, trigger: Trigger) {
    // The server is opening a session the page picked: the page waits for it
    // rather than leave the server on a session the screen no longer shows.
    if switching(app) {
        return;
    }
    let page = &mut app.sessions_page;
    page.open = true;
    page.trigger = trigger;
    page.browse.search.focused = false;
    page.browse.focus = Column::Sessions;
    page.help = false;
    page.rename = None;
    page.busy = None;
    page.ticket += 1;
    if trigger == Trigger::Startup {
        app.session.resume_picker_shown = true;
    }
    if starting(app) {
        app.sessions_page.awaiting_server = true;
        app.sessions_page.loading = true;
    } else if !app.sessions_page.loading {
        load::refresh(app, client);
    }
    // After the refresh started, so a session it has yet to list stays the one
    // to land on. At startup the current session is the fresh one: the
    // highlight goes to the latest other one instead.
    let kept = (trigger != Trigger::Startup)
        .then(|| highlighted_id(app))
        .flatten();
    relayout_on(app, kept);
    crate::telemetry::session_search_opened(app, trigger.name());
}

/// The page shows its loader: the spinner ticks while it does.
pub fn loading_shown(app: &App) -> bool {
    app.sessions_page.open && app.sessions_page.loading
}

/// The attached session's latest reply was shown here, so it is not unread.
pub fn mark_current_seen(app: &App, client: &Arc<Client>) {
    if let Some(id) = app.session.session_id.clone() {
        requests::mark_seen(app, client, id);
    }
}

/// The app server finished starting: read the listing now, so the page opens on it at once.
pub fn server_ready(app: &mut App, client: &Arc<Client>) {
    let waited = std::mem::take(&mut app.sessions_page.awaiting_server);
    if waited || !app.sessions_page.loading {
        load::refresh(app, client);
    }
}

fn starting(app: &App) -> bool {
    matches!(app.session.status, crate::app::Status::Starting)
}

/// Drop a selection over the list or the preview: their text is about to move or go.
pub fn clear_selections(app: &mut App) {
    crate::selection::clear_region(app, crate::selection::RegionId::BottomApp);
    crate::selection::clear_region(app, crate::selection::RegionId::Preview);
}

/// Close the page, back on the session it was opened from.
pub fn close(app: &mut App) {
    // Its selection was over its own text, which the conversation now covers.
    clear_selections(app);
    app.sessions_page.open = false;
    app.sessions_page.rename = None;
    // A read the highlight was settling on would fetch what nothing shows.
    preview::cancel(app);
    // A switch still in flight is cancelled: its late answer is dropped.
    app.sessions_page.busy = None;
    app.sessions_page.ticket += 1;
    if app.sessions_page.trigger == Trigger::Startup {
        app.session.initial_prompt = None;
        app.sessions_page.trigger = Trigger::Left;
    }
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Page {
            generation,
            sessions,
            next,
        } => load::page(app, client, generation, sessions, next),
        Event::Projects {
            generation,
            projects,
        } => {
            // An older open's answer must not replace the current grouping.
            if generation != app.sessions_page.generation {
                return;
            }
            app.sessions_page.projects = projects;
            relayout(app);
        }
        Event::Failed {
            generation,
            message,
        } => {
            // An older load, or one the closed page no longer shows, fails silently.
            let page = &mut app.sessions_page;
            if generation != page.generation {
                return;
            }
            page.loading = false;
            page.reloading = false;
            page.incoming.clear();
            if page.open {
                crate::notices::inline(app, crate::app::Severity::Error, &message);
            }
        }
        Event::Leases(snapshot) => leases::apply_snapshot(app, snapshot),
        Event::Preview(event) => preview::apply_event(app, event),
        Event::Action(event) => actions::apply_event(app, client, event),
    }
}

/// Whether the current session can be left now: no turn, job, queue or shell command.
pub fn idle(app: &App) -> bool {
    crate::commands::submission::accepts_idle_command(app)
}

/// Adopt `state` as the attached session and leave the page: shared by open and new.
pub fn adopted(app: &mut App, client: &Arc<Client>, state: PublicSessionState, verb: &str) {
    switch::landed(app);
    // The next open lands on the session just opened, once it is listed.
    app.sessions_page.browse.highlight = Some(state.session.id.clone());
    adopt::adopt(app, client, &state, verb);
    app.sessions_page.open = false;
    app.sessions_page.busy = None;
    app.sessions_page.trigger = Trigger::Left;
}
