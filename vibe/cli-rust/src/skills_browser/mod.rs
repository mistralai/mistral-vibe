//! `/skills` browser: installed skills by name, and shared ones to import.

pub mod bodies;
pub mod catalog;
mod changes;
mod keys;
pub mod labels;
pub mod list;
pub mod model;
pub mod page;
pub mod picker;
pub mod pins;
mod request;

use std::collections::HashMap;
use std::rc::Rc;
use std::sync::Arc;

use ratatui::layout::Rect;
use serde_json::Value;

use crate::app::App;
use crate::clock::UiInstant;
use crate::commands::spawn_session;
use crate::commands::submission::new_message_id;
use crate::hints::Hint;
use crate::option_list::{MultiAction, OptionList, OptionView};
use crate::search_field::Search;
use crate::server::{Client, InstalledSkill, SkillCatalog, SkillUpdate};
use crate::transcript::local;
use crate::ui::visual_lines::VisualLine;
use bodies::{BodyKey, BodyPane};
use catalog::Catalog;
use list::Row;
use model::Body;
use page::Page;
use picker::Picker;

pub use bodies::{scroll_body_to, wheel_body};
pub use keys::{handle_key, mouse, paste};
pub use list::Tab;
pub use request::{Event, Opening, Outcome};

/// The open browser.
#[derive(Default)]
pub struct State {
    /// While set the browser replaces the input box.
    pub open: bool,
    /// Every installed pin in server order, the shadowed ones too.
    pub installed: Vec<InstalledSkill>,
    /// The shared skills sorted by name, and what the session can do with them.
    pub catalog: Catalog,
    /// The catalog is still being read.
    pub catalog_pending: bool,
    /// Registry skills are on, which reads the catalog and shows the Shared tab.
    pub registry: bool,
    /// The skill page shown instead of the list.
    pub page: Option<Page>,
    pub search: Search,
    /// Each tab's rows, built together, so another tab's are at hand for its label and the box height.
    pub tab_rows: Vec<(Tab, Vec<Row>)>,
    pub list: OptionList<MultiAction>,
    /// The list viewport from the last render, whichever list it showed.
    pub list_area: Rect,
    /// The list's tab; without registry skills the list is My Skills alone.
    pub tab: Tab,
    /// Where each tab was drawn, for clicks.
    pub tab_areas: Vec<(Tab, Rect)>,
    /// The skill whose removal waits for a second `d`.
    pub removing: Option<String>,
    /// Where and when a click last opened a screen, so the second press of that double-click lands nowhere.
    pub screen_click: Option<((u16, u16), UiInstant)>,
    /// The press being dropped, until its release.
    pub dropping: bool,
    /// The label of the change in flight; another is refused until it answers.
    pub busy: Option<String>,
    /// Counts the openings, so a body fetched for an earlier one is dropped.
    pub generation: u64,
    /// The list or page waiting for its answer.
    pub opening: Option<Opening>,
    /// Bodies fetched or being fetched for this opening: at most one per version brought on screen, and gone with the opening, as opening and closing start an empty map.
    pub bodies: HashMap<BodyKey, Body>,
    /// The body under the rows of whichever screen is open.
    pub pane: BodyPane,
    /// The body pane's lines and the width they were wrapped to, until the next change drops them.
    body_lines: Option<(usize, Rc<Vec<VisualLine>>)>,
}

/// What the browser shows: the list, a skill's page, or one of its scope pickers.
pub enum Screen<'a> {
    List,
    Page(&'a Page),
    Picker(&'a Page, &'a Picker),
}

/// A screen without its borrows, to tell whether the browser moved to another.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    List,
    Page,
    Picker,
}

impl Screen<'_> {
    pub fn kind(&self) -> Kind {
        match self {
            Self::List => Kind::List,
            Self::Page(_) => Kind::Page,
            Self::Picker(..) => Kind::Picker,
        }
    }
}

impl State {
    pub fn screen(&self) -> Screen<'_> {
        match &self.page {
            None => Screen::List,
            Some(page) => match &page.picker {
                Some(picker) => Screen::Picker(page, picker),
                None => Screen::Page(page),
            },
        }
    }
}

/// Whether the runtime turns registry skills on.
pub fn registry_enabled(runtime: &Value) -> bool {
    runtime
        .pointer("/runtime/config/experimentalEnableRegistrySkills")
        .and_then(Value::as_bool)
        .unwrap_or(false)
}

/// `/skills`: read the installed pins and open; with registry skills on the catalog follows on its own, so a slow registry never holds the list back.
pub fn show(app: &mut App, client: &Arc<Client>) {
    let started = spawn_session(app, client, |client, session_id| async move {
        Event::Opened(request::installed(&client, &session_id).await)
    });
    if started {
        app.skills_browser.opening = Some(Opening::List);
        loading(app, labels::LOADING);
    }
}

/// Once a session is ready, announce the pinned skills that have a newer version.
pub fn notify_updates(app: &mut App, client: &Arc<Client>, runtime: &Value) {
    if !registry_enabled(runtime) {
        return;
    }
    spawn_session(app, client, |client, session_id| async move {
        Event::Updates(request::updates(&client, &session_id).await)
    });
}

pub fn apply_event(app: &mut App, client: &Arc<Client>, event: Event) {
    match event {
        Event::Opened(Ok(installed)) => {
            let registry = registry_enabled(&app.session.runtime);
            open(app, installed, registry);
            if registry {
                read_catalog(app, client);
            }
        }
        Event::Catalog {
            generation,
            catalog,
        } => catalog_loaded(app, generation, catalog),
        Event::Opened(Err(error)) => {
            if matches!(app.skills_browser.opening, Some(Opening::List)) {
                app.skills_browser.opening = None;
            }
            add_error(app, &labels::read_failed(&error));
        }
        Event::Page { row, versions } => page::loaded(app, row, versions),
        Event::Body {
            generation,
            key,
            body,
        } => bodies::loaded(app, generation, key, body),
        Event::Changed { outcome, installed } => changes::changed(app, outcome, installed),
        Event::Updates(Ok(updates)) if !updates.is_empty() => announce_updates(app, &updates),
        Event::Updates(_) => {}
    }
    bodies::sync(app, client);
    resume_loading(app);
}

/// The footer of whichever list is on screen.
pub fn help(state: &State) -> Vec<Hint> {
    match state.screen() {
        Screen::List => list::help(state),
        Screen::Page(page) => page::help(state, page),
        Screen::Picker(_, picker) => picker::help(picker),
    }
}

/// The list on screen, for the shared wheel, scrollbar and hover.
pub fn visible_list(state: &mut State) -> &mut dyn OptionView {
    match state.page.as_mut() {
        Some(page) => page.visible_list(),
        None => &mut state.list,
    }
}

fn open(app: &mut App, installed: Vec<InstalledSkill>, registry: bool) {
    if app.skills_browser.open {
        return;
    }
    app.skills_browser = State {
        open: true,
        installed,
        catalog_pending: registry,
        registry,
        busy: app.skills_browser.busy.take(),
        generation: app.skills_browser.generation + 1,
        ..State::default()
    };
    list::reset(&mut app.skills_browser);
}

/// Read the shared skills catalog for the opening on screen.
fn read_catalog(app: &mut App, client: &Arc<Client>) {
    let generation = app.skills_browser.generation;
    spawn_session(app, client, move |client, session_id| async move {
        let catalog = request::catalog(&client, &session_id).await;
        Event::Catalog {
            generation,
            catalog,
        }
    });
}

/// The catalog for this opening fills the Shared tab, the update badges and the Project rows; one for an earlier opening is dropped.
fn catalog_loaded(app: &mut App, generation: u64, catalog: SkillCatalog) {
    let state = &mut app.skills_browser;
    if !state.open || generation != state.generation {
        return;
    }
    state.catalog = Catalog::new(catalog);
    state.catalog_pending = false;
    list::refresh(state);
    page::refresh(state);
}

/// Close the browser; a change in flight outlives it and keeps the spinner under its label, and the spinner of a screen still loading ends.
pub(super) fn close(app: &mut App) {
    let opening = app.skills_browser.opening.take();
    app.skills_browser = State {
        busy: app.skills_browser.busy.take(),
        generation: app.skills_browser.generation,
        ..State::default()
    };
    if let Some(label) = app.skills_browser.busy.clone() {
        return loading(app, &label);
    }
    let shown = opening.is_some_and(|opening| app.view.loading.label() == opening.label());
    if shown {
        app.view.command_loading = false;
    }
}

/// File the announcement with the startup warnings, or as a row once a turn ran, so it can be read back.
fn announce_updates(app: &mut App, updates: &[SkillUpdate]) {
    let names: Vec<&str> = updates.iter().map(|update| update.name.as_str()).collect();
    crate::notices::warn(app, &labels::updates(&names));
}

/// Show the command spinner with `label` while a round-trip is in flight, unless a turn owns it.
pub(super) fn loading(app: &mut App, label: &str) {
    if app.begin_command_loading() {
        app.view.loading.set_label(label);
    }
}

/// Every answer ends the spinner; a change, a list or a page still waiting keeps its own.
fn resume_loading(app: &mut App) {
    let state = &app.skills_browser;
    let opening = state
        .opening
        .as_ref()
        .map(|opening| opening.label().to_owned());
    if let Some(label) = state.busy.clone().or(opening) {
        loading(app, &label);
    }
}

fn add_error(app: &mut App, text: &str) {
    local::add_command_error(&mut app.view.transcript, &new_message_id(), text);
}
