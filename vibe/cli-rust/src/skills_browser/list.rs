//! The `/skills` list: the My Skills and Shared tabs, their rows and what the keys do on them.

use std::sync::Arc;

use caseless::default_case_fold_str as casefold;

use super::labels::{self, MY_SKILLS, NO_MATCH, NO_SKILLS, SHARED};
use super::pins::{importable, listed, matches};
use super::State;
use super::{files, page};
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
    /// The heading over the skills that failed to load.
    BrokenHeading,
    /// A skill file or manifest that failed to load, by its index in the issues.
    Broken(usize),
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

/// A tab's rows: the installed skills by name, one row per name, or the shared skills to add by name; a query keeps only matches.
fn tab_rows(state: &State, tab: Tab) -> Vec<Row> {
    let query = casefold(state.search.query.trim());
    let (columns, entries, empty) = match tab {
        Tab::Mine => (Row::InstalledColumns, mine(state, &query), NO_SKILLS),
        Tab::Shared => {
            let note = labels::shared_note(state);
            (Row::CatalogColumns, shared(state, &query), note)
        }
    };
    let broken = match tab {
        Tab::Mine => broken(state, &query),
        Tab::Shared => Vec::new(),
    };
    let mut rows = Vec::new();
    if !entries.is_empty() {
        rows.push(columns);
        rows.extend(entries);
    } else if broken.is_empty() {
        rows.push(Row::Note(if query.is_empty() { empty } else { NO_MATCH }));
    }
    if !broken.is_empty() {
        rows.push(Row::BrokenHeading);
        rows.extend(broken);
    }
    rows
}

fn broken(state: &State, query: &str) -> Vec<Row> {
    let issues = state.issues.iter().enumerate();
    issues
        .filter(|(_, issue)| matches(query, &issue.file, &issue.message))
        .map(|(index, _)| Row::Broken(index))
        .collect()
}

/// The installed skills by name, whatever their scope or source.
fn mine(state: &State, query: &str) -> Vec<Row> {
    let mut pins: Vec<_> = state
        .installed
        .iter()
        .filter(|pin| listed(state, &pin.name) == Some(*pin))
        .filter(|pin| matches(query, &pin.name, &pin.description))
        .collect();
    pins.sort_by_cached_key(|pin| casefold(&pin.name));
    pins.into_iter()
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

/// The tabs and their labels, none without registry skills; each label counts the skills it lists, its matches while searching.
pub fn tabs(state: &State) -> Vec<(Tab, tab_bar::Label)> {
    if !state.registry {
        return Vec::new();
    }
    let label = |(tab, rows): &(Tab, Vec<Row>)| {
        let name = match tab {
            Tab::Mine => MY_SKILLS,
            Tab::Shared => SHARED,
        };
        let skills = rows.iter().filter(|row| row.selectable()).count();
        let label = tab_bar::Label {
            name,
            count: Some(skills),
        };
        (*tab, label)
    };
    state.tab_rows.iter().map(label).collect()
}

/// Show `tab`'s rows from its first one; only with registry skills on.
pub(super) fn open_tab(state: &mut State, tab: Tab) {
    if !state.registry {
        return;
    }
    state.tab = tab;
    reset(state);
}

/// The highlighted skill's row.
pub fn highlighted(state: &State) -> Option<&Row> {
    state
        .rows()
        .get(state.list.selected())
        .filter(|row| row.selectable())
}

/// The pin the highlighted row shows, if it shows an installed skill.
pub(super) fn highlighted_pin(state: &State) -> Option<&InstalledSkill> {
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

/// Moves, then Enter, then the highlighted skill's keys, then the list's own, then Esc.
pub(super) fn help(state: &State) -> Vec<Hint> {
    let mut hints = navigate(state.rows().iter().any(Row::selectable));
    if highlighted(state).is_some() {
        hints.push((key::ENTER, action::VIEW));
    }
    hints.extend(files::pin_hints(state));
    if !state.search.focused {
        hints.push(("n", action::NEW));
    }
    if state.registry && !state.search.focused {
        hints.push((key::TAB_LEFT_RIGHT, action::SWITCH_TAB));
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
