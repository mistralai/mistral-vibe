//! Tabs on a browser's title row: their labels, and the one a press lands on.

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::{Position, Rect};

/// A tab's label: its name, then how many rows match while a query is typed.
pub fn label(name: &str, matches: Option<usize>) -> String {
    match matches {
        Some(count) => format!("{name} {count}"),
        None => name.to_owned(),
    }
}

/// The tab, among those drawn at `areas`, a left press lands on.
pub fn pressed<T: Copy>(areas: &[(T, Rect)], event: &MouseEvent) -> Option<T> {
    if event.kind != MouseEventKind::Down(MouseButton::Left) {
        return None;
    }
    let at = Position::new(event.column, event.row);
    let tab = areas.iter().find(|(_, area)| area.contains(at));
    tab.map(|(tab, _)| *tab)
}
