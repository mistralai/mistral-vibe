//! `/trusted-folders` bottom-app: title, search field, the two sections, shortcut hint.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::Frame;

use super::browser_box::{self, draw_frame};
use super::theme;
use super::trusted_folders_add as adding;
use super::visual_lines::{merge, text, wrap_indented, VisualLine};
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::trusted_folders::rows::{self, Row};

/// Draw the whole screen with the browser replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let max_visible = area.height as usize / 2;
    let kind = super::bottom_app::Kind::TrustedFolders;
    if let Some(add) = app.trusted_folders.add.as_ref() {
        let lines = browser_box::fit(area.width, max_visible, |width| adding::lay_out(add, width));
        let height = lines.len().min(max_visible) as u16 + adding::chrome(add, area.width);
        return super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
            adding::draw_box(app, f, area, &lines)
        });
    }
    let lines = browser_box::fit(area.width, max_visible, |width| lay_out(app, width));
    let visible = lines.len().min(max_visible) as u16;
    let help = rows::help(&app.trusted_folders);
    let height = visible + browser_box::chrome(true, area.width, &help);
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &lines)
    });
}

/// Folder rows stay on one line, clipped; headings and notes wrap.
fn lay_out(app: &App, width: usize) -> Vec<VisualLine> {
    let state = &app.trusted_folders;
    let mut out = Vec::new();
    for (index, row) in rows::rows(state).iter().enumerate() {
        let highlighted = index == state.list.selected() && row.selectable();
        let (base, dim) = super::list_cursor::styles(highlighted);
        let disabled = base.fg(theme::muted());
        let wrapped = match row {
            Row::Entry(..) => {
                let path = rows::path(state, row).unwrap_or_default();
                let mut chars = text(&format!("  {}", rows::label(&path)), base);
                chars.truncate(width);
                vec![chars]
            }
            Row::Heading(heading, count) => {
                let mut chars = text(heading, disabled.add_modifier(Modifier::BOLD));
                chars.extend(text(&format!(" · {count}"), dim));
                vec![chars]
            }
            Row::Note(note) => wrap_indented(&format!("  {note}"), disabled, width),
            Row::Blank => vec![Vec::new()],
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

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, lines: &[VisualLine]) {
    let help = rows::help(&app.trusted_folders);
    let chrome = browser_box::chrome(true, area.width, &help);
    if area.height < chrome {
        return;
    }
    draw_frame(f, area, &rows::title(&app.trusted_folders));
    let target = MouseTarget::TrustedFolders;
    super::search_field::draw_under_title(
        app,
        f,
        area,
        |app| &mut app.trusted_folders.filter,
        "Search folders",
        target,
    );
    let visible = area.height.saturating_sub(chrome);
    let list_area = browser_box::pane(area, true, 0, visible);
    app.trusted_folders.list_area = list_area;
    browser_box::draw_list(app, f, area, list_area, lines, target);
    browser_box::draw_help(f, area, &help);
}
