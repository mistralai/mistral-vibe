//! `/plugins` option rows: the filtered plugin list plus what failed to load, or one plugin's detail.

use super::text;
use super::State;
use crate::hints::{self, action, key, Hint};
use crate::search_field;
use crate::server::PluginCatalogEntry;

/// One option of the list, as Python's `PluginsApp._refresh_view` adds them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// A selectable plugin, by index into the catalogue.
    Entry(usize),
    /// A disabled plain line, such as the empty-list notice.
    Note(&'static str),
    Blank,
    /// The bold `Not loaded` heading above the dropped files.
    Heading(&'static str),
    /// A disabled dim line: a dropped file or a detail fact.
    Dim(String),
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Entry(_))
    }
}

pub fn rows(state: &State) -> Vec<Row> {
    if let Some(entry) = viewing_entry(state) {
        return text::detail_lines(entry)
            .into_iter()
            .map(Row::Dim)
            .collect();
    }
    let query = state.query();
    let mut rows: Vec<Row> = state
        .catalog
        .plugins
        .iter()
        .enumerate()
        .filter(|(_, entry)| text::matches(entry, &query))
        .map(|(index, _)| Row::Entry(index))
        .collect();
    if rows.is_empty() {
        rows.push(Row::Note(if query.is_empty() {
            "No plugins in this session"
        } else {
            "No plugins match this filter"
        }));
    }
    if !state.catalog.dropped.is_empty() {
        rows.push(Row::Blank);
        rows.push(Row::Heading("Not loaded"));
        rows.extend(
            state
                .catalog
                .dropped
                .iter()
                .map(|dropped| Row::Dim(text::dropped_line(&dropped.file, &dropped.message))),
        );
    }
    rows
}

/// The plugin whose detail is shown; `None` in the list view or once a reload removed it.
pub fn viewing_entry(state: &State) -> Option<&PluginCatalogEntry> {
    let name = state.viewing.as_deref()?;
    state
        .catalog
        .plugins
        .iter()
        .find(|entry| entry.name == name)
}

pub fn title(state: &State) -> String {
    match viewing_entry(state) {
        Some(entry) => entry.name.clone(),
        None => format!("Plugins · {} in this session", state.catalog.plugins.len()),
    }
}

/// The hint line as `(key, label)` pairs (Python `_LIST_VIEW_HELP` and friends).
pub fn help(state: &State) -> Vec<Hint> {
    if viewing_entry(state).is_some() {
        return vec![("r", action::RELOAD), hints::BACK];
    }
    let filter = &state.filter;
    search_field::hints(
        filter.focused,
        !filter.query.is_empty(),
        &[
            hints::NAVIGATE,
            (key::ENTER, action::VIEW),
            hints::SEARCH,
            ("r", action::RELOAD),
            hints::CLOSE,
        ],
    )
}
