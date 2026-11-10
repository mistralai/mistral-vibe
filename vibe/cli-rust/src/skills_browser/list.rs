//! The `/skills` list: the My Skills and Shared tabs, their rows and what the keys do on them.

use std::sync::Arc;

use caseless::default_case_fold_str as casefold;

use super::changes;
use super::labels::{self, MY_SKILLS, NO_MATCH, NO_SKILLS, SHARED};
use super::page;
use super::pins::{importable, listed, matches, of_skill};
use super::State;
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::search_field;
use crate::server::{Client, InstalledSkill};
use crate::tab_bar;

/// One option of the list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// The dim column names over the installed skills.
    InstalledColumns,
    /// The dim column names over the shared skills.
    CatalogColumns,
    /// An installed skill, by the name it resolves.
    Installed(String),
    /// A shared skill, by its skill id.
    Catalog(String),
    /// A plain line, such as an empty tab's note.
    Note(&'static str),
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Installed(_) | Self::Catalog(_))
    }
}

/// The list's two tabs while registry skills are on, named like the Vibe Skills page.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    #[default]
    Mine,
    Shared,
}

impl Tab {
    /// The tab Tab opens from this one; Shift+Tab stays the app's agent cycle.
    pub fn other(self) -> Self {
        match self {
            Self::Mine => Self::Shared,
            Self::Shared => Self::Mine,
        }
    }
}

impl State {
    /// The open tab's rows, as last built.
    pub fn rows(&self) -> &[Row] {
        let open = self.tab_rows.iter().find(|(tab, _)| *tab == self.tab);
        open.map_or(&[], |(_, rows)| rows)
    }
}

/// Build every tab's rows again.
fn build(state: &mut State) {
    let tabs = every_tab(state).iter();
    state.tab_rows = tabs.map(|tab| (*tab, tab_rows(state, *tab))).collect();
}

/// A tab's rows: the installed skills in server order, one row per name, or the shared skills to add by name; a query keeps only matches.
fn tab_rows(state: &State, tab: Tab) -> Vec<Row> {
    let query = casefold(state.search.query.trim());
    let (columns, entries, empty) = match tab {
        Tab::Mine => (Row::InstalledColumns, mine(state, &query), NO_SKILLS),
        Tab::Shared => {
            let note = labels::shared_note(state);
            (Row::CatalogColumns, shared(state, &query), note)
        }
    };
    if entries.is_empty() {
        let note = if query.is_empty() { empty } else { NO_MATCH };
        return vec![Row::Note(note)];
    }
    let mut rows = vec![columns];
    rows.extend(entries);
    rows
}

fn mine(state: &State, query: &str) -> Vec<Row> {
    state
        .installed
        .iter()
        .filter(|pin| listed(state, &pin.name) == Some(*pin))
        .filter(|pin| matches(query, &pin.name, &pin.description))
        .map(|pin| Row::Installed(pin.name.clone()))
        .collect()
}

fn shared(state: &State, query: &str) -> Vec<Row> {
    importable(state)
        .filter(|entry| matches(query, &entry.name, &entry.description))
        .map(|entry| Row::Catalog(entry.skill_id.clone()))
        .collect()
}

/// The tabs the list switches between: My Skills alone without registry skills.
fn every_tab(state: &State) -> &'static [Tab] {
    match state.registry {
        true => &[Tab::Mine, Tab::Shared],
        false => &[Tab::Mine],
    }
}

/// Whether any tab lists a skill to highlight.
pub fn any_skill(state: &State) -> bool {
    let mut rows = state.tab_rows.iter().flat_map(|(_, rows)| rows);
    rows.any(Row::selectable)
}

/// The tabs and their labels, none without registry skills; while searching each label counts its matches.
pub fn tabs(state: &State) -> Vec<(Tab, String)> {
    if !state.registry {
        return Vec::new();
    }
    let searching = !state.search.query.trim().is_empty();
    let label = |(tab, rows): &(Tab, Vec<Row>)| {
        let name = match tab {
            Tab::Mine => MY_SKILLS,
            Tab::Shared => SHARED,
        };
        let matches = rows.iter().filter(|row| row.selectable()).count();
        (*tab, tab_bar::label(name, searching.then_some(matches)))
    };
    state.tab_rows.iter().map(label).collect()
}

/// Show `tab`'s rows from its first one, a removal disarmed; only with registry skills on.
pub(super) fn open_tab(state: &mut State, tab: Tab) {
    if !state.registry {
        return;
    }
    state.tab = tab;
    state.removing = None;
    reset(state);
}

/// The highlighted skill's row.
pub(super) fn highlighted(state: &State) -> Option<&Row> {
    state
        .rows()
        .get(state.list.selected())
        .filter(|row| row.selectable())
}

/// The pin the highlighted row shows, if it shows an installed skill.
fn highlighted_pin(state: &State) -> Option<&InstalledSkill> {
    match highlighted(state)? {
        Row::Installed(name) => listed(state, name),
        _ => None,
    }
}

/// New pins under the list: keep the highlight on its skill, else near where it was.
pub(super) fn refresh(state: &mut State) {
    let kept = highlighted(state).cloned();
    let index = rebuild(state, kept.as_ref());
    state.list.highlight(index);
}

/// A fresh open or a new filter: the first selectable row, the viewport at the top.
pub(super) fn reset(state: &mut State) {
    build(state);
    let first = state.rows().iter().position(Row::selectable).unwrap_or(0);
    state.list.select(first);
    state.list.reset_viewport();
}

/// Back on the list: on `from`'s tab and on `from` while it is listed, else near where the highlight was.
pub(super) fn focus(state: &mut State, from: &Row) {
    match from {
        Row::Installed(_) => state.tab = Tab::Mine,
        Row::Catalog(_) if state.registry => state.tab = Tab::Shared,
        _ => {}
    }
    let index = rebuild(state, Some(from));
    state.list.select(index);
}

/// Build the rows again; where `row` now is while it is listed, else the selectable row nearest where the highlight was.
fn rebuild(state: &mut State, row: Option<&Row>) -> usize {
    let near = state.list.selected();
    build(state);
    let rows = state.rows();
    let index = row.and_then(|row| rows.iter().position(|candidate| candidate == row));
    index.or_else(|| nearest(state, near)).unwrap_or(0)
}

/// The selectable row closest to `index`, the upper one on a tie.
fn nearest(state: &State, index: usize) -> Option<usize> {
    let selectable = state.rows().iter().enumerate();
    selectable
        .filter(|(_, row)| row.selectable())
        .map(|(row, _)| row)
        .min_by_key(|row| (row.abs_diff(index), *row))
}

/// Enter: open the highlighted skill's page.
pub(super) fn activate(app: &mut App, client: &Arc<Client>) {
    if let Some(row) = highlighted(&app.skills_browser).cloned() {
        page::open(app, client, &row);
    }
}

/// Space: turn the highlighted skill on or off everywhere it is installed, when its name loads it.
pub(super) fn toggle(app: &mut App, client: &Arc<Client>) {
    let pin = highlighted_pin(&app.skills_browser).filter(|pin| pin.active);
    if let Some(pin) = pin.cloned() {
        changes::toggle(app, client, &pin);
    }
}

/// `d`: arm the highlighted shared skill's removal, and remove it from every scope on the second press; a local skill is not removed here.
pub(super) fn remove(app: &mut App, client: &Arc<Client>, armed: Option<String>) {
    let state = &mut app.skills_browser;
    let Some((name, skill_id)) = highlighted_pin(state)
        .and_then(|pin| Some((pin.name.clone(), pin.registry_id()?.to_owned())))
    else {
        return;
    };
    if armed.as_deref() != Some(&name) {
        state.removing = Some(name);
        return;
    }
    changes::remove_everywhere(app, client, &name, &skill_id);
}

pub(super) fn help(state: &State) -> Vec<Hint> {
    let mut hints = navigate(state.rows().iter().any(Row::selectable));
    match highlighted(state) {
        Some(Row::Installed(_)) => {
            hints.push((key::ENTER, action::VIEW));
            if let Some(pin) = highlighted_pin(state) {
                if pin.active && !pin.locked {
                    hints.push((key::SPACE, switch(pin.enabled)));
                }
                if pin.registry_id().is_some() {
                    hints.push(("d", action::REMOVE));
                }
            }
        }
        Some(Row::Catalog(skill_id)) if of_skill(state, skill_id).is_some() => {
            hints.push((key::ENTER, action::VIEW));
        }
        Some(Row::Catalog(_)) => hints.push((key::ENTER, action::ADD)),
        _ => {}
    }
    if state.registry && !state.search.focused {
        hints.push((key::TAB, action::SWITCH_TAB));
    }
    hints.extend([hints::SEARCH, hints::CLOSE]);
    let filtered = !state.search.query.is_empty();
    search_field::hints(state.search.focused, filtered, &hints)
}

/// Navigation is only offered when there is a row to move to.
pub(super) fn navigate(movable: bool) -> Vec<Hint> {
    movable.then_some(hints::NAVIGATE).into_iter().collect()
}

/// What Space would do: the opposite of the skill's state.
pub(super) fn switch(enabled: bool) -> &'static str {
    if enabled {
        action::DISABLE
    } else {
        action::ENABLE
    }
}
