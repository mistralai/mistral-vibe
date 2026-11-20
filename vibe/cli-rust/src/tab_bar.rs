//! Tabs on a browser's title row: their labels, and the one a press lands on.

use crossterm::event::{MouseButton, MouseEvent, MouseEventKind};
use ratatui::layout::{Position, Rect};

/// A tab's label: its name, and how many rows it lists, drawn apart so the number never reads as part of the name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Label {
    pub name: &'static str,
    pub count: Option<usize>,
}

impl Label {
    /// The label as plain text, `My Skills 30`.
    pub fn text(&self) -> String {
        match self.count {
            Some(count) => format!("{} {count}", self.name),
            None => self.name.to_owned(),
        }
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
