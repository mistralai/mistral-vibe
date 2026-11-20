//! `/plugins` browser bottom-app: title, search field, plugin option list, shortcut hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;

use super::browser_box::{self, draw_frame};
use super::theme;
use super::visual_lines::{merge, text, wrap_folded, wrap_indented, Sc, VisualLine};
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::plugins::rows::{self, Row};
use crate::plugins::text::{entry_facts, DetailLine, LABEL_WIDTH};
use crate::server::PluginCatalogEntry;

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let max_visible = area.height as usize / 2;
    let lines = browser_box::fit(area.width, max_visible, |width| lay_out(app, width));
    let visible = lines.len().min(max_visible) as u16;
    let kind = super::bottom_app::Kind::Plugins;
    let help = rows::help(&app.plugins);
    let height = visible + browser_box::chrome(search_shown(app), area.width, &help);
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &lines)
    });
}

/// The list view shows its search row under the title, like `/mcp` and `/config`.
fn search_shown(app: &App) -> bool {
    app.plugins.viewing.is_none()
}

/// Plugin rows stay on one line; notes and facts wrap.
fn lay_out(app: &App, width: usize) -> Vec<VisualLine> {
    let state = &app.plugins;
    let plugins = &state.catalog.plugins;
    let rows = rows::rows(state);
    let name_width = rows
        .iter()
        .filter_map(|row| match row {
            Row::Entry(index) => Some(plugins[*index].name.chars().count()),
            _ => None,
        })
        .max()
        .unwrap_or(0);
    let mut out = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let highlighted = index == state.list.selected() && row.selectable();
        let (base, dim) = styles(highlighted);
        let wrapped = match row {
            Row::Entry(entry) => {
                let mut chars = entry_chars(&plugins[*entry], name_width, base, dim);
                chars.truncate(width);
                vec![chars]
            }
            Row::Note(note) => wrap_indented(note, disabled(base), width),
            Row::Blank => vec![Vec::new()],
            Row::Heading(heading) => {
                let bold = disabled(base).add_modifier(Modifier::BOLD);
                wrap_indented(heading, bold, width)
            }
            Row::Dim(line) => wrap_indented(line, dim, width),
            Row::Detail(line) => detail_chars(line, base, dim, width),
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

/// Disabled options take `$text-disabled`.
fn disabled(base: Style) -> Style {
    base.fg(theme::muted())
}

/// The block cursor bolds the whole highlighted option, dim facts included.
fn styles(highlighted: bool) -> (Style, Style) {
    super::list_cursor::styles(highlighted)
}

/// `  name  scope · format · digest`, plus drift and uninstall notes.
fn entry_chars(entry: &PluginCatalogEntry, width: usize, base: Style, dim: Style) -> Vec<Sc> {
    let mut chars = text(&format!("  {:<width$}", entry.name), base);
    chars.extend(text(&format!("  {}", entry_facts(entry)), dim));
    if entry.drifted > 0 {
        let warning = super::list_cursor::accent(base, theme::text_warning());
        chars.extend(text(&format!(" · ⚠ {} drifted", entry.drifted), warning));
    }
    if entry.installed_root.is_none() {
        chars.extend(text(" · uninstalled since pin", dim));
    }
    chars
}

/// A detail line: the description and values in the foreground, labels dim, the heading bold, each component kind in the primary color; values wrap under themselves.
fn detail_chars(line: &DetailLine, base: Style, dim: Style, width: usize) -> Vec<Vec<Sc>> {
    let (label, label_style, value) = match line {
        DetailLine::Blank => return vec![Vec::new()],
        DetailLine::Description(text) => return wrap_indented(&format!("  {text}"), base, width),
        DetailLine::Heading(heading) => {
            let bold = base.add_modifier(Modifier::BOLD);
            return wrap_indented(&format!("  {heading}"), bold, width);
        }
        DetailLine::Fact { label, value } => (*label, dim, value),
        DetailLine::Components { kind, names } => (*kind, base.fg(theme::text_primary()), names),
    };
    let indent = 2 + LABEL_WIDTH;
    let room = width.saturating_sub(indent).max(1);
    let mut out = Vec::new();
    for (index, row) in wrap_folded(&text(value, base), room)
        .into_iter()
        .enumerate()
    {
        let head = if index == 0 { label } else { "" };
        let mut chars = text("  ", base);
        chars.extend(text(&format!("{head:<LABEL_WIDTH$}"), label_style));
        chars.extend(row);
        out.push(chars);
    }
    out
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, lines: &[VisualLine]) {
    let search = search_shown(app);
    let help = rows::help(&app.plugins);
    let chrome = browser_box::chrome(search, area.width, &help);
    if area.height < chrome {
        return;
    }
    draw_frame(f, area, &rows::title(&app.plugins));
    if search {
        let target = MouseTarget::Plugins;
        let placeholder = "Search plugins";
        super::search_field::draw_under_title(
            app,
            f,
            area,
            |app| &mut app.plugins.filter,
            placeholder,
            target,
        );
    }
    let visible = area.height.saturating_sub(chrome);
    let list_area = browser_box::pane(area, search, 0, visible);
    app.plugins.list_area = list_area;
    browser_box::draw_list(app, f, area, list_area, lines, MouseTarget::Plugins);
    browser_box::draw_help(f, area, &help);
}
