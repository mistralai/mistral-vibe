//! The `/trusted-folders` add view: a path field, and the folders it offers to trust.

use std::sync::Arc;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers, MouseEvent};
use serde_json::json;

use super::request;
use super::{Event, Target};
use crate::app::App;
use crate::option_list::{Click, MultiAction, OptionList};
use crate::search_field::{self, Search};
use crate::server::{method, Client, TrustFolderCandidate, TrustFolderInspection};

/// Candidates a PageUp or PageDown moves by.
const PAGE: usize = 5;

/// The open add view.
#[derive(Default)]
pub struct Add {
    pub field: Search,
    /// The server's answer for the field, `None` until the first one lands.
    pub inspection: Option<TrustFolderInspection>,
    /// The highlighted candidate: Enter trusts it, Tab writes it into the field.
    pub candidates: OptionList<MultiAction>,
    /// Numbers each inspect request so a late answer for older text is dropped.
    pub generation: u64,
    /// The request the shown candidates answer; Enter waits until it is the latest.
    pub answered: u64,
    /// Why the server could not list folders for the field, shown in place of them.
    pub error: Option<String>,
    /// This view sent the add in flight, so its answer closes it.
    pub saving: bool,
}

impl Add {
    pub fn candidates(&self) -> &[TrustFolderCandidate] {
        self.inspection
            .as_ref()
            .map_or(&[], |inspection| inspection.candidates.as_slice())
    }

    pub fn highlighted(&self) -> Option<&TrustFolderCandidate> {
        self.candidates().get(self.candidates.selected())
    }

    /// Whether Enter would trust the highlighted candidate, which must answer the field as typed.
    pub fn addable(&self) -> bool {
        self.answered == self.generation
            && self
                .highlighted()
                .is_some_and(|c| c.problem.is_none() && c.stored.as_deref() != Some("trusted"))
    }
}

/// `a`: open on the session's folder, followed by a separator so its children are offered at once.
pub fn open(app: &mut App, client: &Arc<Client>) {
    let separator = std::path::MAIN_SEPARATOR;
    let start = app
        .session
        .cwd
        .as_deref()
        .map(|cwd| format!("{}{separator}", cwd.trim_end_matches(separator)))
        .unwrap_or_default();
    let mut field = Search {
        focused: true,
        ..Search::default()
    };
    search_field::paste(&mut field, &start);
    app.trusted_folders.add = Some(Add {
        field,
        ..Add::default()
    });
    inspect(app, client);
}

/// The add view owns keys while open.
pub fn handle_key(app: &mut App, client: &Arc<Client>, key: KeyEvent) {
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    let plain = !key
        .modifiers
        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT | KeyModifiers::SUPER);
    let count = add.candidates().len();
    match key.code {
        KeyCode::Esc => app.trusted_folders.add = None,
        KeyCode::Enter if plain => save(app, client),
        KeyCode::Tab if plain => complete(app, client),
        KeyCode::Up | KeyCode::Down if count > 0 => {
            let down = key.code == KeyCode::Down;
            add.candidates.step(down, count, true, |_| true);
        }
        KeyCode::PageUp | KeyCode::PageDown => {
            add.candidates
                .page(key.code == KeyCode::PageDown, count, PAGE);
        }
        _ => {
            let edited = crate::keymap::action_for(&key)
                .is_some_and(|action| search_field::edit(&mut add.field, action));
            if edited {
                inspect(app, client);
            }
        }
    }
}

/// A paste keeps its first line, a path never spanning two.
pub fn paste(app: &mut App, client: &Arc<Client>, text: &str) {
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    let line = crate::utils::text::first_line(text).trim();
    if search_field::paste(&mut add.field, line) {
        inspect(app, client);
    }
}

/// A click highlights a candidate; a click on the highlighted one trusts it, like Enter.
pub fn mouse(app: &mut App, client: &Arc<Client>, event: MouseEvent) {
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    if let Some(Click::Confirm(_)) = add.candidates.mouse(event) {
        save(app, client);
    }
}

/// Tab: write the highlighted candidate into the field, which then offers its children.
fn complete(app: &mut App, client: &Arc<Client>) {
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    let Some(value) = add.highlighted().map(|candidate| candidate.value.clone()) else {
        return;
    };
    search_field::clear(&mut add.field);
    add.field.focused = true;
    search_field::paste(&mut add.field, &value);
    inspect(app, client);
}

/// Ask the server which folders the field offers.
pub fn inspect(app: &mut App, client: &Arc<Client>) {
    let cwd = app.session.cwd.clone();
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    add.generation = add.generation.wrapping_add(1);
    let generation = add.generation;
    let params = json!({ "path": add.field.query, "cwd": cwd });
    let inspect = method::WORKSPACE_TRUST_FOLDERS_INSPECT;
    request::spawn(app, client, inspect, params, move |answer| {
        Event::Inspected(generation, answer)
    });
}

/// A current answer replaces the candidates, back on the first; a failed one leaves none, so Enter never trusts a folder the field no longer names.
pub fn apply_inspection(
    app: &mut App,
    generation: u64,
    answer: Result<TrustFolderInspection, String>,
) {
    let Some(add) = app.trusted_folders.add.as_mut() else {
        return;
    };
    if generation != add.generation {
        return;
    }
    add.error = answer.as_ref().err().cloned();
    let inspection = answer.unwrap_or_default();
    if add.candidates() != inspection.candidates.as_slice() {
        add.candidates.reset();
    }
    add.inspection = Some(inspection);
    add.answered = generation;
}

/// Enter: trust the highlighted candidate.
fn save(app: &mut App, client: &Arc<Client>) {
    let cwd = app.session.cwd.clone();
    if app.trusted_folders.updating {
        return;
    }
    let Some(add) = app.trusted_folders.add.as_ref() else {
        return;
    };
    if !add.addable() {
        return;
    }
    let Some(path) = add.highlighted().map(|candidate| candidate.path.clone()) else {
        return;
    };
    let params = json!({ "path": path, "cwd": cwd });
    let wrap = move |answer| Event::Updated {
        path,
        target: Target::Added,
        answer,
    };
    let add_method = method::WORKSPACE_TRUST_FOLDERS_ADD;
    let saving = request::spawn(app, client, add_method, params, wrap);
    app.trusted_folders.updating = saving;
    if let Some(add) = app.trusted_folders.add.as_mut() {
        add.saving = saving;
    }
}
