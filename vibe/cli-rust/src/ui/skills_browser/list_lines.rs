//! The `/skills` list as styled lines.

use ratatui::style::Style;

use super::columns::{
    self, Columns, GAP, INSTALLED_HEADINGS, MARKER_WIDTH, NAME_HEADING, SHARING_GAP,
    SHARING_HEADING, VERSION_HEADING,
};
use crate::skills_browser::labels;
use crate::skills_browser::list::Row;
use crate::skills_browser::pins::{listed, update};
use crate::skills_browser::State;
use crate::ui::list_cursor;
use crate::ui::theme;
use crate::ui::visual_lines::{merge, text, wrap_indented, Sc, VisualLine};

/// Every row as lines `width` wide: rows clip at the edge, notes wrap.
pub(super) fn lay_out(state: &State, width: usize) -> Vec<VisualLine> {
    let columns = Columns::fit(state, state.rows(), width);
    let selected = state.list.selected();
    let mut out = Vec::new();
    for (index, row) in state.rows().iter().enumerate() {
        let highlighted = index == selected && row.selectable();
        let (base, dim) = list_cursor::styles(highlighted);
        let wrapped = match row {
            Row::Note(note) => note_lines(note, base.fg(theme::muted()), width),
            Row::Broken(index) => broken_lines(state, *index, width),
            Row::BrokenHeading => vec![
                Vec::new(),
                text(&format!("  {}", labels::FAILED_TO_LOAD), dim),
            ],
            _ => vec![chars(state, row, &columns, base, dim)],
        };
        let option = row.selectable().then_some(index);
        out.extend(wrapped.into_iter().map(|chars| VisualLine {
            option,
            highlighted,
            spans: merge(&chars),
        }));
    }
    out
}

fn note_lines(note: &str, style: Style, width: usize) -> Vec<Vec<Sc>> {
    wrap_indented(&format!("  {note}"), style, width)
}

/// `  ✕ file`, then its reason wrapped under it.
fn broken_lines(state: &State, index: usize, width: usize) -> Vec<Vec<Sc>> {
    let Some(issue) = state.issues.get(index) else {
        return Vec::new();
    };
    let (base, dim) = list_cursor::styles(false);
    let mut lines = wrap_indented(
        &format!("  ✕ {}", issue.file),
        base.fg(theme::text_error()),
        width,
    );
    lines.extend(wrap_indented(&format!("    {}", issue.message), dim, width));
    lines
}

fn chars(state: &State, row: &Row, columns: &Columns, base: Style, dim: Style) -> Vec<Sc> {
    match row {
        Row::InstalledColumns => installed_heading(columns, dim),
        Row::CatalogColumns => catalog_heading(columns, dim),
        Row::Installed(name) => installed(state, name, columns, base, dim),
        Row::Catalog(skill_id) => catalog(state, skill_id, columns, base, dim),
        Row::Note(_) | Row::Broken(_) | Row::BrokenHeading => Vec::new(),
    }
}

fn installed_heading(columns: &Columns, dim: Style) -> Vec<Sc> {
    let mut chars = text(&" ".repeat(MARKER_WIDTH), dim);
    chars.extend(text(&list_cursor::padded(NAME_HEADING, columns.name), dim));
    let headings = INSTALLED_HEADINGS.map(str::to_owned);
    chars.extend(cells(&headings, &columns.installed, dim));
    chars
}

fn catalog_heading(columns: &Columns, dim: Style) -> Vec<Sc> {
    let mut chars = text(&" ".repeat(MARKER_WIDTH), dim);
    chars.extend(text(&list_cursor::padded(NAME_HEADING, columns.name), dim));
    let headings = [SHARING_HEADING, VERSION_HEADING].map(str::to_owned);
    chars.extend(catalog_cells(headings, columns, dim));
    chars
}

/// `  ● name  Scope  Source  Version  ● New vN`: ● on, ○ off, colored by whether it is locked, on the cursor bar too so the highlighted row still shows its state; `(shadowed)` when another pin loads its version under another name.
fn installed(state: &State, name: &str, columns: &Columns, base: Style, dim: Style) -> Vec<Sc> {
    let Some(pin) = listed(state, name) else {
        return Vec::new();
    };
    let glyph = if pin.enabled { "●" } else { "○" };
    let glyph_style = match (pin.locked, pin.enabled) {
        (true, _) => base.fg(theme::text_warning()),
        (false, true) => base.fg(list_cursor::current_color()),
        (false, false) => dim,
    };
    let mut chars = text("  ", base);
    chars.extend(text(glyph, glyph_style));
    chars.extend(text(" ", base));
    chars.extend(text(&list_cursor::padded(&pin.name, columns.name), base));
    chars.extend(cells(&labels::cells(pin), &columns.installed, dim));
    if !pin.active {
        chars.extend(text(&" ".repeat(GAP), base));
        chars.extend(text(labels::SHADOWED, dim));
    }
    if let Some(version) = update(state, pin) {
        let badge = list_cursor::accent(base, theme::text_primary());
        chars.extend(text(&" ".repeat(GAP), base));
        chars.extend(text(&columns::badge(version), badge));
    }
    chars
}

/// Each drawn column's cell, after its gap.
fn cells(values: &[String; 3], widths: &[usize; 3], style: Style) -> Vec<Sc> {
    let mut chars = Vec::new();
    for (value, width) in values.iter().zip(widths) {
        if *width > 0 {
            let cell = format!("{}{}", " ".repeat(GAP), list_cursor::padded(value, *width));
            chars.extend(text(&cell, style));
        }
    }
    chars
}

/// `    name   Sharing  vN`.
fn catalog(state: &State, skill_id: &str, columns: &Columns, base: Style, dim: Style) -> Vec<Sc> {
    let Some(entry) = state.catalog.entry(skill_id) else {
        return Vec::new();
    };
    let mut chars = text(&" ".repeat(MARKER_WIDTH), base);
    chars.extend(text(&list_cursor::padded(&entry.name, columns.name), base));
    let values = [labels::sharing(entry), labels::latest(entry)];
    chars.extend(catalog_cells(values, columns, dim));
    chars
}

/// The Sharing and Version cells the pane keeps, after their gaps; the last one unpadded.
fn catalog_cells([sharing, version]: [String; 2], columns: &Columns, style: Style) -> Vec<Sc> {
    let mut tail = String::new();
    if columns.sharing > 0 {
        tail.push_str(&" ".repeat(SHARING_GAP));
        match columns.version {
            0 => tail.push_str(&sharing),
            _ => tail.push_str(&list_cursor::padded(&sharing, columns.sharing)),
        }
    }
    if columns.version > 0 {
        tail.push_str(&" ".repeat(GAP));
        tail.push_str(&version);
    }
    text(&tail, style)
}
