//! `/trusted-folders`: browse the persisted trust decisions, toggle, forget or add one.

pub mod add;
pub mod add_text;
mod keys;
mod request;
pub mod rows;

use std::sync::Arc;

use ratatui::layout::Rect;
use serde_json::json;

use crate::app::App;
use crate::commands::submission::new_message_id;
use crate::option_list::{MultiAction, OptionList, OptionView};
use crate::search_field::{self, Search};
use crate::server::{method, Client, TrustFolderInspection, WorkspaceTrustFolders};
use crate::transcript::local;
pub use keys::{handle_key, mouse};
use request::Answer;
/// Lines one wheel notch scrolls the option list.
const WHEEL_STEP: usize = 2;

/// Where an update moves a stored folder.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    Trusted,
    Untrusted,
    Undecided,
    /// A folder the add view trusted.
    Added,
}

impl Target {
    /// The `action` property of `vibe.trusted_folder.updated`.
    pub(crate) fn action(self) -> &'static str {
        match self {
            Self::Trusted => "trust",
            Self::Untrusted => "untrust",
            Self::Undecided => "forget",
            Self::Added => "add",
        }
    }

    pub(crate) fn wire(self) -> &'static str {
        match self {
            Self::Trusted | Self::Added => "trusted",
            Self::Untrusted => "untrusted",
            Self::Undecided => "undecided",
        }
    }
}

/// The open browser.
#[derive(Default)]
pub struct State {
    /// While set the browser replaces the input box.
    pub open: bool,
    pub folders: WorkspaceTrustFolders,
    pub filter: Search,
    /// Enter or `d` act on the highlight, so a click highlights before it toggles.
    pub list: OptionList<MultiAction>,
    /// Option-list viewport from the last render.
    pub list_area: Rect,
    /// A list update or an add is in flight; further ones are dropped so answers never cross.
    /// It outlives closing and reopening the browser, since the request does too.
    pub updating: bool,
    /// The add view, which replaces the list while open.
    pub add: Option<add::Add>,
}

impl State {
    /// The filter query as matched: stripped and case-folded.
    pub fn query(&self) -> String {
        caseless::default_case_fold_str(self.filter.query.trim())
    }
}

pub enum Event {
    Read(Answer),
    /// An add-view check, numbered by the request that asked for it.
    Inspected(u64, Answer<TrustFolderInspection>),
    Updated {
        path: String,
        target: Target,
        answer: Answer,
    },
}

/// `/trusted-folders`: read the trust store, then open the browser on it.
pub fn show(app: &mut App, client: &Arc<Client>) {
    let list = method::WORKSPACE_TRUST_FOLDERS_LIST;
    request::spawn(app, client, list, json!({}), Event::Read);
}

pub fn apply_event(app: &mut App, event: Event) {
    match event {
        Event::Read(Ok(folders)) => open(app, folders),
        Event::Inspected(generation, answer) => add::apply_inspection(app, generation, answer),
        Event::Read(Err(error)) => {
            add_error(app, &format!("Failed to read trusted folders: {error}"))
        }
        Event::Updated {
            path,
            target,
            answer,
        } => {
            app.trusted_folders.updating = false;
            // Only the add view that sent the save closes on it, not one reopened since.
            let saved_here = app
                .trusted_folders
                .add
                .as_mut()
                .is_some_and(|add| std::mem::take(&mut add.saving));
            match answer {
                Ok(folders) => {
                    add_status(app, &rows::changed(&path, target));
                    crate::telemetry::trusted_folder_updated(app, target.action());
                    if target == Target::Added && saved_here {
                        app.trusted_folders.add = None;
                    }
                    if app.trusted_folders.open {
                        app.trusted_folders.folders = folders;
                        settle_highlight(app);
                    }
                }
                Err(error) => add_error(app, &format!("Failed to update {path}: {error}")),
            }
        }
    }
}

fn open(app: &mut App, folders: WorkspaceTrustFolders) {
    if app.trusted_folders.open {
        return;
    }
    app.trusted_folders = State {
        open: true,
        folders,
        updating: app.trusted_folders.updating,
        ..State::default()
    };
    reset_highlight(app);
}

/// Esc: close the browser, keeping the guard of an update still in flight.
pub fn close(app: &mut App) {
    app.trusted_folders = State {
        updating: app.trusted_folders.updating,
        ..State::default()
    };
}

/// Highlight the first folder with the viewport at the top.
pub(crate) fn reset_highlight(app: &mut App) {
    let first = selectable(app).first().copied().unwrap_or(0);
    app.trusted_folders.list.select(first);
    app.trusted_folders.list.reset_viewport();
}

/// After an update, keep the highlight where it was: on the row that took the moved folder's place.
fn settle_highlight(app: &mut App) {
    let selectable = selectable(app);
    let current = app.trusted_folders.list.selected();
    let next = selectable
        .iter()
        .find(|index| **index >= current)
        .or(selectable.last())
        .copied()
        .unwrap_or(0);
    app.trusted_folders.list.select(next);
}

pub(crate) fn selectable(app: &App) -> Vec<usize> {
    rows::rows(&app.trusted_folders)
        .iter()
        .enumerate()
        .filter_map(|(index, row)| row.selectable().then_some(index))
        .collect()
}

pub fn paste(app: &mut App, client: &Arc<Client>, text: &str) {
    if app.trusted_folders.add.is_some() {
        return add::paste(app, client, text);
    }
    if search_field::paste(&mut app.trusted_folders.filter, text) {
        reset_highlight(app);
    }
}

/// The wheel scrolls the viewport without moving the highlight.
pub fn wheel(app: &mut App, down: bool) {
    visible_list(&mut app.trusted_folders).wheel(down, WHEEL_STEP);
}

/// The list the box shows: the add view's completions while it is open.
pub fn visible_list(state: &mut State) -> &mut dyn OptionView {
    match state.add.as_mut() {
        Some(add) => &mut add.candidates,
        None => &mut state.list,
    }
}

fn add_status(app: &mut App, text: &str) {
    local::add_command_status(&mut app.view.transcript, &new_message_id(), text);
}

fn add_error(app: &mut App, text: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), text);
}
