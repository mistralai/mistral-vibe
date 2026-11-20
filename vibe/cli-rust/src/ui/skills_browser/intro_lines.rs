//! The highlighted skill's description above the list, at a fixed height so the rows never move, and the rules that close a block.

use unicode_width::UnicodeWidthStr;

use crate::skills_browser::labels::NO_DESCRIPTION;
use crate::skills_browser::list::{self, Row};
use crate::skills_browser::pins::listed;
use crate::skills_browser::State;
use crate::ui::list_cursor;
use crate::ui::theme;
use crate::ui::visual_lines::{merge, text, wrap_indented, Sc, VisualLine};

/// The description's lines; a longer one ends in `…`.
pub(super) const TEXT_LINES: usize = 2;

/// The description, cut or padded to `TEXT_LINES`, then a rule.
pub(super) fn lay_out(state: &State, width: usize) -> Vec<VisualLine> {
    let (base, dim) = list_cursor::styles(false);
    let mut lines = match description(state) {
        Some(description) => clipped(description, base, width),
        None => wrap_indented(&format!("  {NO_DESCRIPTION}"), dim, width),
    };
    lines.truncate(TEXT_LINES);
    lines.resize(TEXT_LINES, Vec::new());
    let mut out: Vec<VisualLine> = lines.into_iter().map(|chars| line(&chars)).collect();
    out.push(rule("", width));
    out
}

/// The highlighted skill's description, none when it has none.
fn description(state: &State) -> Option<&str> {
    let text = match list::highlighted(state)? {
        Row::Installed(name) => &listed(state, name)?.description,
        Row::Catalog(skill_id) => &state.catalog.entry(skill_id)?.description,
        _ => return None,
    };
    let text = text.trim();
    (!text.is_empty()).then_some(text)
}

/// The description on one paragraph, wrapped, its last kept line ending in `…` when more is cut.
fn clipped(description: &str, style: ratatui::style::Style, width: usize) -> Vec<Vec<Sc>> {
    let flat = description.split_whitespace().collect::<Vec<_>>().join(" ");
    let mut lines = wrap_indented(&format!("  {flat}"), style, width);
    if lines.len() > TEXT_LINES {
        lines.truncate(TEXT_LINES);
        if let Some(last) = lines.last_mut() {
            while last.len() > 2 && columns(last) + 1 > width {
                last.pop();
            }
            last.push(('…', style));
        }
    }
    lines
}

fn columns(chars: &[Sc]) -> usize {
    chars
        .iter()
        .map(|(c, _)| c.to_string())
        .collect::<String>()
        .width()
}

/// A rule across `width`, `label` near its start when there is one, all in the box border's color so it stays behind the text.
pub(super) fn rule(label: &str, width: usize) -> VisualLine {
    let style = theme::border_style().bg(theme::background());
    let mut chars = Vec::new();
    if !label.is_empty() {
        chars.extend(text(&format!("── {label} "), style));
    }
    let fill = width.saturating_sub(columns(&chars));
    chars.extend(text(&"─".repeat(fill), style));
    line(&chars)
}

fn line(chars: &[Sc]) -> VisualLine {
    VisualLine {
        option: None,
        highlighted: false,
        spans: merge(chars),
    }
}
