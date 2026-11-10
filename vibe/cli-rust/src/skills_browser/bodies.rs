//! The body pane under every screen's rows: what it shows, fetched one version at a time, scrolled on its own.

use std::rc::Rc;
use std::sync::Arc;

use super::labels::NO_SESSION;
use super::list::{self, Row};
use super::model::{self, Body, LATEST};
use super::page::{Page, Property};
use super::picker;
use super::pins::{effective_choice, listed, page_pin, scope_pin};
use super::{request, Event, Kind, Screen, State};
use crate::app::App;
use crate::commands::spawn_session;
use crate::server::{Client, InstalledSkill, SkillScope};
use crate::ui::skills_browser::wrap_body;
use crate::ui::visual_lines::VisualLine;

/// A fetched body's skill id and version, the newest when `None`.
pub type BodyKey = (String, Option<i64>);

/// What a pane shows: an installed pin's own body, or a registry version.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Content {
    Pin {
        name: String,
        scope: String,
        source: String,
    },
    Version(BodyKey),
}

impl Content {
    fn of(pin: &InstalledSkill) -> Self {
        Self::Pin {
            name: pin.name.clone(),
            scope: pin.scope.clone(),
            source: pin.source.clone(),
        }
    }
}

/// The body under the rows: on which screen, so moving to another starts its body at the top, what, and the description a shared row shows above it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Shown {
    pub screen: Kind,
    pub content: Content,
    pub intro: Option<String>,
}

/// The pane's scroll over what it last showed.
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct BodyPane {
    pub shown: Option<Shown>,
    pub scroll: usize,
    /// The body's lines and how many of them fit, as last rendered.
    pub lines: usize,
    pub visible: usize,
}

impl BodyPane {
    /// Scroll by `amount` lines, stopping at either end.
    pub fn scroll_by(&mut self, down: bool, amount: usize) {
        let target = if down {
            self.scroll.saturating_add(amount)
        } else {
            self.scroll.saturating_sub(amount)
        };
        self.scroll_to(target);
    }

    /// Scroll by the lines the pane shows, stopping at either end.
    pub fn page(&mut self, down: bool) {
        self.scroll_by(down, self.visible.max(1));
    }

    /// Put line `position` at the top, as far as the body reaches.
    pub fn scroll_to(&mut self, position: usize) {
        self.scroll = position.min(self.lines.saturating_sub(self.visible));
    }
}

/// What the highlight resolves to: a pin it shows as it is, or a registry version to fetch.
enum Shows<'a> {
    Pin(&'a InstalledSkill),
    Version(BodyKey),
}

/// The screen, what its highlight shows, and the description a shared row shows above it.
type Resolved<'a> = (Kind, Shows<'a>, Option<String>);

/// The body under the highlight: on the list the skill a row shows, on a page the highlighted scope's pin (else the page's) or the version an install would pin, in a picker the highlighted version.
pub fn shown(state: &State) -> Option<Shown> {
    let (screen, shows, intro) = resolve(state)?;
    let content = match shows {
        Shows::Pin(pin) => Content::of(pin),
        Shows::Version(key) => Content::Version(key),
    };
    Some(Shown {
        screen,
        content,
        intro,
    })
}

/// The shown body with its description: a pin's prompt, or the fetched version as far as it got.
pub fn view(state: &State) -> Option<(Option<String>, Body)> {
    let (_, shows, intro) = resolve(state)?;
    let body = match shows {
        Shows::Pin(pin) => Body::Ready(pin.prompt.clone()),
        Shows::Version(key) => state.bodies.get(&key).cloned().unwrap_or(Body::Loading),
    };
    Some((intro, body))
}

impl State {
    /// The body pane's lines at `width`, wrapped once per change: every change to the browser ends in `sync`, which drops them.
    pub(crate) fn body_lines(&mut self, width: usize) -> Rc<Vec<VisualLine>> {
        if let Some((wrapped, lines)) = &self.body_lines {
            if *wrapped == width {
                return Rc::clone(lines);
            }
        }
        let lines = Rc::new(match view(self) {
            Some((intro, body)) => wrap_body(intro.as_deref(), &body, width),
            None => Vec::new(),
        });
        self.body_lines = Some((width, Rc::clone(&lines)));
        lines
    }
}

/// The wheel over the body pane scrolls it.
pub fn wheel_body(app: &mut App, down: bool, step: usize) {
    app.skills_browser.pane.scroll_by(down, step);
}

/// A drag on the body pane's scrollbar.
pub fn scroll_body_to(app: &mut App, position: usize) {
    app.skills_browser.pane.scroll_to(position);
}

/// A body fetched for this opening; one for an earlier opening is dropped.
pub(super) fn loaded(app: &mut App, generation: u64, key: BodyKey, body: Result<String, String>) {
    let state = &mut app.skills_browser;
    if state.open && state.generation == generation {
        state
            .bodies
            .insert(key, body.map_or_else(Body::Failed, Body::Ready));
    }
}

/// Whether the screen keeps a body pane whatever is highlighted: a page and a picker always, the list once any of its tabs has a skill, so Tab never moves the rows.
pub fn reserved(state: &State) -> bool {
    state.page.is_some() || list::any_skill(state)
}

fn resolve(state: &State) -> Option<Resolved<'_>> {
    let screen = state.screen();
    let (shows, intro) = match screen {
        Screen::List => list_shows(state)?,
        Screen::Page(page) => (page_shows(state, page)?, None),
        Screen::Picker(page, _) => (Shows::Version(picker::previewed(state, page)?), None),
    };
    Some((screen.kind(), shows, intro))
}

fn list_shows(state: &State) -> Option<(Shows<'_>, Option<String>)> {
    match list::highlighted(state)? {
        Row::Installed(name) => Some((Shows::Pin(listed(state, name)?), None)),
        Row::Catalog(skill_id) => {
            let entry = state.catalog.entry(skill_id)?;
            let key = (skill_id.clone(), Some(entry.latest_version));
            let intro = Some(entry.description.clone()).filter(|text| !text.is_empty());
            Some((Shows::Version(key), intro))
        }
        _ => None,
    }
}

fn page_shows<'a>(state: &'a State, page: &Page) -> Option<Shows<'a>> {
    let scope = match page.rows.get(page.list.selected()) {
        Some(Property::Scope(scope)) => Some(*scope),
        _ => None,
    };
    if page.subject.installed() {
        let pin = scope.and_then(|scope| scope_pin(state, page, scope));
        return Some(Shows::Pin(pin.or_else(|| page_pin(state, page))?));
    }
    let skill_id = page.subject.skill_id()?.to_owned();
    let version = install_version(state, page, scope);
    Some(Shows::Version((skill_id, version)))
}

/// The version an install would pin for `scope`, by the same rule as the scope pickers.
fn install_version(state: &State, page: &Page, scope: Option<SkillScope>) -> Option<i64> {
    match effective_choice(state, page, scope) {
        Some(choice) => model::version_of(&page.versions, &choice),
        None => model::resolve(&page.versions, LATEST),
    }
}

/// Bring the pane in line with the screen after any change: the lines last wrapped go, another body starts at its top, a failed one is forgotten so its visit fetches it again, and a missing one is fetched once nothing else is in flight.
pub(super) fn sync(app: &mut App, client: &Arc<Client>) {
    let state = &mut app.skills_browser;
    state.body_lines = None;
    if !state.open {
        return;
    }
    let now = shown(state);
    if state.pane.shown != now {
        let failed = version_key(now.as_ref())
            .filter(|key| matches!(state.bodies.get(*key), Some(Body::Failed(_))));
        if let Some(key) = failed {
            state.bodies.remove(key);
        }
        state.pane = BodyPane {
            shown: now,
            ..BodyPane::default()
        };
    }
    let missing = version_key(state.pane.shown.as_ref())
        .filter(|key| !state.bodies.contains_key(*key))
        .cloned();
    let in_flight = state.bodies.values().any(|body| *body == Body::Loading);
    if let Some(key) = missing.filter(|_| !in_flight) {
        fetch(app, client, key);
    }
}

fn version_key(shown: Option<&Shown>) -> Option<&BodyKey> {
    match shown.map(|shown| &shown.content) {
        Some(Content::Version(key)) => Some(key),
        _ => None,
    }
}

fn fetch(app: &mut App, client: &Arc<Client>, key: BodyKey) {
    let (skill_id, version) = key.clone();
    let answer = key.clone();
    let generation = app.skills_browser.generation;
    let started = spawn_session(app, client, move |client, session_id| async move {
        let body = request::body(&client, &session_id, &skill_id, version).await;
        Event::Body {
            generation,
            key: answer,
            body,
        }
    });
    let body = if started {
        Body::Loading
    } else {
        Body::Failed(NO_SESSION.to_owned())
    };
    app.skills_browser.bodies.insert(key, body);
}

#[cfg(test)]
mod tests;
