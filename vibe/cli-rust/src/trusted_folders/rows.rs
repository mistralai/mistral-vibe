//! `/trusted-folders` option rows: a trusted and an untrusted section, filtered by the search.

use super::{State, Target};
use crate::hints::{self, action, key, Hint};
use crate::search_field;
use crate::utils::paths::collapse_home;

/// One line of the list.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// The bold section title.
    Heading(&'static str, usize),
    /// A stored folder: whether it is trusted, and its index in that list.
    Entry(bool, usize),
    /// A disabled plain line, such as an empty section.
    Note(&'static str),
    Blank,
}

impl Row {
    pub fn selectable(&self) -> bool {
        matches!(self, Self::Entry(..))
    }
}

pub fn rows(state: &State) -> Vec<Row> {
    let query = state.query();
    let mut rows = Vec::new();
    for (trusted, heading) in [(true, "Trusted"), (false, "Untrusted")] {
        let entries: Vec<Row> = section(state, trusted)
            .iter()
            .enumerate()
            .filter(|(_, path)| matches(path, &query))
            .map(|(index, _)| Row::Entry(trusted, index))
            .collect();
        if !rows.is_empty() {
            rows.push(Row::Blank);
        }
        rows.push(Row::Heading(heading, entries.len()));
        if entries.is_empty() {
            rows.push(Row::Note(if query.is_empty() {
                "None"
            } else {
                "No folders match this filter"
            }));
        }
        rows.extend(entries);
    }
    rows
}

pub fn section(state: &State, trusted: bool) -> &[String] {
    match trusted {
        true => &state.folders.trusted,
        false => &state.folders.untrusted,
    }
}

/// The stored path a row shows, as the trust file writes it.
pub fn path(state: &State, row: &Row) -> Option<String> {
    let Row::Entry(trusted, index) = row else {
        return None;
    };
    section(state, *trusted).get(*index).cloned()
}

/// The path as shown, with the home directory folded to `~`.
pub fn label(path: &str) -> String {
    collapse_home(path)
}

fn matches(path: &str, query: &str) -> bool {
    query.is_empty() || caseless::default_case_fold_str(&label(path)).contains(query)
}

/// The notice an applied change shows; sessions already running keep their config.
pub fn changed(path: &str, target: Target) -> String {
    let path = label(path);
    match target {
        Target::Trusted => format!("Trusted {path}. Its project config loads the next time Vibe starts."),
        Target::Untrusted => format!("Untrusted {path}. Its project config is ignored the next time Vibe starts."),
        Target::Undecided => format!("Forgot the decision for {path}."),
        Target::Added => format!(
            "Trusted {path} and the folders inside it. Their project config loads the next time Vibe starts."
        ),
    }
}

pub fn title(state: &State) -> String {
    let count = state.folders.trusted.len() + state.folders.untrusted.len();
    format!("Trusted folders · {count} stored")
}

/// Enter and `f` act on the highlighted folder, so their hints show only when one is.
pub fn help(state: &State) -> Vec<Hint> {
    let filter = &state.filter;
    let mut list = vec![hints::NAVIGATE];
    if let Some(Row::Entry(trusted, _)) = rows(state).get(state.list.selected()) {
        let enter = if *trusted {
            action::UNTRUST
        } else {
            action::TRUST
        };
        list.extend([(key::ENTER, enter), ("f", action::FORGET)]);
    }
    list.extend([("a", action::ADD), hints::SEARCH, hints::CLOSE]);
    search_field::hints(filter.focused, !filter.query.is_empty(), &list)
}
