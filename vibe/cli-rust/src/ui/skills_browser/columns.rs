//! The `/skills` list columns and which of them fit the pane.

use unicode_width::UnicodeWidthStr;

use crate::server::SkillCatalogEntry;
use crate::skills_browser::labels::{cells, latest, sharing};
use crate::skills_browser::list::Row;
use crate::skills_browser::pins::{listed, update};
use crate::skills_browser::State;

pub const NAME_HEADING: &str = "Skill";
/// The installed columns, dropped from the right when a pane is too narrow.
pub const INSTALLED_HEADINGS: [&str; 3] = ["Scope", "Source", "Version"];
pub const SHARING_HEADING: &str = "Sharing";
pub const VERSION_HEADING: &str = "Version";
/// The `  ● ` before an installed name, blank before a shared one.
pub const MARKER_WIDTH: usize = 4;
/// The blank between two columns.
pub const GAP: usize = 2;
/// The blank before the Sharing column.
pub const SHARING_GAP: usize = 3;

/// The update marker drawn after an installed row's columns.
pub fn badge(version: i64) -> String {
    format!("● New v{version}")
}

/// Column widths for the rows on screen; a column is `0` when it is not drawn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Columns {
    pub name: usize,
    pub installed: [usize; 3],
    pub sharing: usize,
    pub version: usize,
    /// The width the rows are laid out to.
    pub pane: usize,
}

impl Columns {
    /// Size every column to its widest value or heading, then drop columns from the right until a row fits `pane`: Version, Source and Scope on My Skills, Version and Sharing on Shared.
    pub fn fit(state: &State, rows: &[Row], pane: usize) -> Self {
        let pins: Vec<_> = rows
            .iter()
            .filter_map(|row| match row {
                Row::Installed(name) => listed(state, name),
                _ => None,
            })
            .collect();
        let entries: Vec<_> = rows
            .iter()
            .filter_map(|row| match row {
                Row::Catalog(skill_id) => state.catalog.entry(skill_id),
                _ => None,
            })
            .collect();
        let names = pins.iter().map(|pin| pin.name.width());
        let name = names
            .chain(entries.iter().map(|entry| entry.name.width()))
            .chain([NAME_HEADING.width()])
            .max()
            .unwrap_or(0);
        let cells: Vec<[String; 3]> = pins.iter().map(|pin| cells(pin)).collect();
        let widths = std::array::from_fn(|column| {
            let widest = cells.iter().map(|row| row[column].width()).max();
            match widest {
                None | Some(0) => 0,
                Some(widest) => widest.max(INSTALLED_HEADINGS[column].width()),
            }
        });
        let badge_width = pins
            .iter()
            .filter_map(|pin| update(state, pin))
            .map(|version| GAP + badge(version).width())
            .max()
            .unwrap_or(0);
        let column_width = |label: fn(&SkillCatalogEntry) -> String, heading: &str| {
            let values = entries.iter().map(|entry| label(entry).width());
            values.chain([heading.width()]).max().unwrap_or(0)
        };
        let shared = [
            column_width(sharing, SHARING_HEADING),
            column_width(latest, VERSION_HEADING),
            0,
        ];
        let [sharing, version, _] = fit(shared, MARKER_WIDTH + name + SHARING_GAP, pane);
        Self {
            name,
            installed: fit(widths, MARKER_WIDTH + name + GAP + badge_width, pane),
            sharing,
            version,
            pane,
        }
    }
}

/// Keep as many leading columns as fit beside `fixed`, dropping from the right.
fn fit(widths: [usize; 3], fixed: usize, pane: usize) -> [usize; 3] {
    for keep in (0..=widths.len()).rev() {
        let mut candidate = widths;
        candidate[keep..].fill(0);
        let drawn: Vec<usize> = candidate.into_iter().filter(|width| *width > 0).collect();
        let needed = fixed + drawn.iter().sum::<usize>() + GAP * drawn.len().saturating_sub(1);
        if needed <= pane {
            return candidate;
        }
    }
    [0; 3]
}
