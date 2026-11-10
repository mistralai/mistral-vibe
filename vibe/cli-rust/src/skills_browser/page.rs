//! A skill's page: its scope rows, its switch, and Remove or Install.

use std::collections::BTreeMap;
use std::sync::Arc;

use super::changes;
use super::labels;
use super::list::{self, navigate, switch, Row};
use super::model::{Choice, Install, Subject, LATEST};
use super::picker::{self, Picker};
use super::pins::{self, listed, of_skill, page_pin, scope_choice, start_scope};
use super::{loading, request, Event, Opening, State};
use crate::app::{App, Severity};
use crate::commands::spawn_session;
use crate::hints::{self, action, key, Hint};
use crate::option_list::{OptionList, SingleAction};
use crate::server::{Client, SkillScope, SkillVersion};

/// An open skill page.
pub struct Page {
    pub subject: Subject,
    pub description: String,
    pub versions: Vec<SkillVersion>,
    pub rows: Vec<Property>,
    pub list: OptionList<SingleAction>,
    /// The version picker of one scope, shown instead of the page.
    pub picker: Option<Picker>,
    /// The list row the page was opened from, to land back on.
    pub from: Row,
}

impl Page {
    /// The list on screen: the picker's while one is open, else the page's.
    pub fn visible_list(&mut self) -> &mut OptionList<SingleAction> {
        match self.picker.as_mut() {
            Some(picker) => &mut picker.list,
            None => &mut self.list,
        }
    }
}

/// A row of a page.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Property {
    /// What one scope pins; Enter opens its version picker.
    Scope(SkillScope),
    Enabled,
    Remove,
    Install,
}

/// A page about to open, waiting for its versions.
pub struct Opened {
    pub subject: Subject,
    pub description: String,
    pub from: Row,
}

/// A shared skill's page waits for its versions; a local or plugin skill's opens at once.
pub(super) fn open(app: &mut App, client: &Arc<Client>, row: &Row) {
    let Some(opened) = opened(&app.skills_browser, row) else {
        return;
    };
    let Some(skill_id) = opened.subject.skill_id().map(str::to_owned) else {
        return show(&mut app.skills_browser, opened, Vec::new());
    };
    let label = labels::opening(opened.subject.name());
    let asked = row.clone();
    let started = spawn_session(app, client, move |client, session_id| async move {
        let versions = request::versions(&client, &session_id, &skill_id).await;
        Event::Page {
            row: asked,
            versions,
        }
    });
    if started {
        let row = row.clone();
        app.skills_browser.opening = Some(Opening::Page {
            row,
            label: label.clone(),
        });
        loading(app, &label);
    }
}

/// An installed row opens what its name resolves to; a shared one its installed page, else an install tracking `latest` globally.
pub fn opened(state: &State, row: &Row) -> Option<Opened> {
    let (subject, description) = match row {
        Row::Installed(name) => {
            let pin = listed(state, name)?;
            let name = pin.name.clone();
            let subject = match pin.registry_id() {
                Some(skill_id) => Subject::Registry {
                    name,
                    skill_id: skill_id.to_owned(),
                    install: Install::Done,
                },
                None => Subject::Plain { name },
            };
            (subject, pin.description.clone())
        }
        Row::Catalog(skill_id) => {
            let entry = state.catalog.entry(skill_id)?;
            let pinned = of_skill(state, skill_id);
            let install = match pinned {
                Some(_) => Install::Done,
                None => Install::Pending(BTreeMap::from([(
                    start_scope(state, entry),
                    Choice::Alias(LATEST.to_owned()),
                )])),
            };
            let subject = Subject::Registry {
                name: pinned.map_or(&entry.name, |pin| &pin.name).clone(),
                skill_id: skill_id.clone(),
                install,
            };
            (subject, entry.description.clone())
        }
        _ => return None,
    };
    Some(Opened {
        subject,
        description,
        from: row.clone(),
    })
}

/// The versions arrived: open the page asked for last, as the pins are now, unless the browser moved on meanwhile.
pub(super) fn loaded(app: &mut App, row: Row, versions: Result<Vec<SkillVersion>, String>) {
    let state = &mut app.skills_browser;
    let asked = matches!(&state.opening, Some(Opening::Page { row: asked, .. }) if *asked == row);
    if !asked {
        return;
    }
    state.opening = None;
    let Some(opened) = opened(state, &row) else {
        return;
    };
    match versions {
        Ok(versions) => show(state, opened, versions),
        Err(error) => {
            let text = labels::versions_failed(opened.subject.name(), &error);
            crate::notices::inline(app, Severity::Error, &text);
        }
    }
}

fn show(state: &mut State, opened: Opened, versions: Vec<SkillVersion>) {
    state.search.focused = false;
    state.removing = None;
    state.opening = None;
    let mut page = Page {
        subject: opened.subject,
        description: opened.description,
        versions,
        rows: Vec::new(),
        list: OptionList::default(),
        picker: None,
        from: opened.from,
    };
    page.rows = rows(state, &page);
    page.list = OptionList::new(landing(&selectable(state, &page), 0));
    state.page = Some(page);
}

/// A shared skill's scopes, Enabled unless locked, then Remove or Install.
pub fn rows(state: &State, page: &Page) -> Vec<Property> {
    let mut rows: Vec<Property> = Vec::new();
    if page.subject.skill_id().is_some() {
        rows.extend(pins::page_scopes(state, page).map(Property::Scope));
    }
    if pins::in_use(state, page) && page_pin(state, page).is_some_and(|pin| !pin.locked) {
        rows.push(Property::Enabled);
    }
    match &page.subject {
        Subject::Registry {
            install: Install::Done,
            ..
        } => rows.push(Property::Remove),
        Subject::Registry { .. } => rows.push(Property::Install),
        Subject::Plain { .. } => {}
    }
    rows
}

/// Which of the page's rows take the highlight.
pub fn selectable(state: &State, page: &Page) -> Vec<bool> {
    page.rows
        .iter()
        .map(|row| free(state, page, *row))
        .collect()
}

/// Whether a row takes the highlight: a scope another skill of the name holds only says so.
fn free(state: &State, page: &Page, row: Property) -> bool {
    !matches!(row, Property::Scope(scope) if pins::taken(state, page, scope))
}

/// `wanted` when it takes the highlight, else the first row that does.
fn landing(selectable: &[bool], wanted: usize) -> usize {
    match selectable.get(wanted) {
        Some(true) => wanted,
        _ => selectable.iter().position(|free| *free).unwrap_or(wanted),
    }
}

/// New pins under the page: its rows again, the highlight on the row it was on, else where it was.
pub(super) fn refresh(state: &mut State) {
    let Some(page) = state.page.as_ref() else {
        return;
    };
    let rows = rows(state, page);
    let picker_rows = page
        .picker
        .as_ref()
        .map(|open| picker::rows(state, page, open.scope));
    let takes: Vec<bool> = rows.iter().map(|row| free(state, page, *row)).collect();
    let Some(page) = state.page.as_mut() else {
        return;
    };
    let selected = page.list.selected();
    let kept = page.rows.get(selected).copied();
    page.rows = rows;
    let index = kept.and_then(|kept| page.rows.iter().position(|row| *row == kept));
    let near = selected.min(page.rows.len().saturating_sub(1));
    page.list.highlight(landing(&takes, index.unwrap_or(near)));
    if let (Some(open), Some(rows)) = (page.picker.as_mut(), picker_rows) {
        picker::refresh(open, rows);
    }
}

/// Enter or a click on a row; Remove asks for a second press, like `/resume`'s delete.
pub(super) fn activate(app: &mut App, client: &Arc<Client>, index: usize, armed: Option<String>) {
    let Some(page) = app.skills_browser.page.as_ref() else {
        return;
    };
    let Some(property) = page.rows.get(index).copied() else {
        return;
    };
    let name = page.subject.name().to_owned();
    match property {
        Property::Scope(scope) => picker::open(app, scope),
        Property::Enabled => {
            let pin = page_pin(&app.skills_browser, page).cloned();
            if let Some(pin) = pin {
                changes::toggle(app, client, &pin);
            }
        }
        Property::Remove if armed.as_deref() == Some(&name) => {
            if let Some(skill_id) = page.subject.skill_id().map(str::to_owned) {
                changes::remove_everywhere(app, client, &name, &skill_id);
            }
        }
        Property::Remove => app.skills_browser.removing = Some(name),
        Property::Install => changes::install(app, client),
    }
}

/// Esc: a picker gives way to its page, on the picker's scope row, a page to the list.
pub(super) fn back(app: &mut App) {
    let state = &mut app.skills_browser;
    let Some(page) = state.page.as_mut() else {
        return;
    };
    match page.picker.take() {
        Some(open) => {
            let row = Property::Scope(open.scope);
            let index = page.rows.iter().position(|candidate| *candidate == row);
            page.list.select(index.unwrap_or(0));
        }
        None => {
            let from = page.from.clone();
            state.page = None;
            list::focus(state, &from);
        }
    }
}

pub(super) fn help(state: &State, page: &Page) -> Vec<Hint> {
    let mut hints = navigate(!page.rows.is_empty());
    hints.extend(
        page.rows
            .get(page.list.selected())
            .and_then(|row| verb(state, page, *row)),
    );
    hints.push(hints::BACK);
    hints
}

fn verb(state: &State, page: &Page, property: Property) -> Option<Hint> {
    Some(match property {
        Property::Scope(scope) if scope_choice(state, page, scope).is_some() => {
            (key::ENTER, action::CHANGE)
        }
        Property::Scope(_) => (key::ENTER, action::PIN),
        Property::Enabled => (key::SPACE_ENTER, switch(page_pin(state, page)?.enabled)),
        Property::Remove => (key::ENTER, action::REMOVE),
        Property::Install => (key::ENTER, action::ADD),
    })
}
