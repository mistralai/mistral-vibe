//! Remote-project bottom panels using the shared transcript, theme, and scrollbar.

pub(super) mod paint;

use ratatui::{
    layout::Rect,
    style::{Modifier, Style},
    widgets::{Block, Borders},
    Frame,
};
use unicode_width::UnicodeWidthStr;

use super::{list_scroll, scrollbar, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::mouse::{register_region, MouseTarget};
use crate::search_field;
use crate::vibe_code_project::items::repo_url_label;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let creating = app.vibe_code_project.create.is_some();
    let rows = app
        .vibe_code_project
        .items
        .len()
        .min(area.height as usize / 2);
    let height = if creating { 10 } else { rows as u16 + 10 };
    app.view.input_area = Rect::default();
    super::bottom_app::draw(
        app,
        f,
        area,
        height,
        super::bottom_app::Kind::RemoteProject,
        |app, f, area| draw_box(app, f, area, creating),
    );
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, creating: bool) {
    if area.width < 8 || area.height < 10 {
        return;
    }
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme::popup_border())),
        area,
    );
    let x = area.x + 2;
    let width = area.width.saturating_sub(4);
    let title = if creating {
        "Create Vibe Code Web project"
    } else {
        "Vibe Code project"
    };
    paint::text(
        f,
        x,
        area.y + 1,
        width,
        title,
        theme::text(theme::primary()).add_modifier(Modifier::BOLD),
    );
    if let Some(view) = &app.vibe_code_project.view {
        let repo = repo_url_label(&view.context.repo_url);
        paint::repository(f, x, area.y + 2, width, &repo, creating);
    }
    if creating {
        draw_create(app, f, area);
    } else {
        draw_picker(app, f, area);
    }
}

fn draw_create(app: &mut App, f: &mut Frame, area: Rect) {
    let state = &mut app.vibe_code_project;
    let Some(create) = &mut state.create else {
        return;
    };
    let x = area.x + 2;
    let width = area.width.saturating_sub(4);
    paint::text(
        f,
        x,
        area.y + 4,
        width,
        "Project name:",
        theme::muted_style(),
    );
    paint::text(
        f,
        x,
        area.y + 6,
        width,
        "Default branch:",
        theme::muted_style(),
    );
    state.search_area = Rect::new(x + 16, area.y + 4, width.saturating_sub(16), 1);
    state.branch_area = Rect::new(x + 16, area.y + 6, width.saturating_sub(16), 1);
    state.list_area = Rect::default();
    paint::field(
        f,
        state.search_area,
        &mut create.name,
        !create.branch_focused,
        app.view.cursor_on,
    );
    paint::field(
        f,
        state.branch_area,
        &mut create.branch,
        create.branch_focused,
        app.view.cursor_on,
    );
    super::hint_line::draw_clipped(
        f,
        x,
        area.y + 8,
        width,
        &[(key::ENTER, action::CREATE), hints::BACK],
    );
    register_region(
        app,
        app.vibe_code_project.search_area,
        MouseTarget::RemoteProject,
    );
    register_region(
        app,
        app.vibe_code_project.branch_area,
        MouseTarget::RemoteProject,
    );
}

fn draw_picker(app: &mut App, f: &mut Frame, area: Rect) {
    let x = area.x + 2;
    let width = area.width.saturating_sub(4);
    paint::text(
        f,
        x,
        area.y + 3,
        width,
        "Only projects linked to this repository are shown.",
        paint::dim(theme::muted_style()),
    );
    paint::text(
        f,
        x,
        area.y + 5,
        width,
        "Search projects:",
        theme::muted_style(),
    );
    let state = &mut app.vibe_code_project;
    state.search_area = Rect::new(x + 17, area.y + 5, width.saturating_sub(17), 1);
    state.branch_area = Rect::default();
    paint::field(
        f,
        state.search_area,
        &mut state.query,
        state.search_focused,
        app.view.cursor_on,
    );
    let visible = area.height.saturating_sub(10) as usize;
    let total = state.items.len();
    state.scroll = match state.free_scroll {
        true => state.scroll.min(total.saturating_sub(visible)),
        false => {
            let items = &state.items;
            let highlight = state.selected..state.selected + 1;
            list_scroll::follow(state.scroll, visible, total, highlight, |line| {
                items[line].selectable()
            })
        }
    };
    let offset = state.scroll;
    let overflow = total > visible;
    state.list_area = Rect::new(x, area.y + 7, width, visible as u16);
    let Some(view) = &state.view else { return };
    let name_width = state
        .items
        .iter()
        .map(|i| i.label(view).width())
        .max()
        .unwrap_or(28)
        .clamp(28, 48);
    for (row, index) in (offset..total).take(visible).enumerate() {
        paint::item(
            f,
            Rect::new(
                x + 1,
                area.y + 7 + row as u16,
                width.saturating_sub(2 + u16::from(overflow)),
                1,
            ),
            state,
            index,
            name_width,
        );
    }
    let state = &app.vibe_code_project;
    let hints = search_field::hints(
        state.search_focused,
        !state.query.text.is_empty(),
        &[hints::NAVIGATE, hints::SELECT, hints::SEARCH, hints::CANCEL],
    );
    super::hint_line::draw_clipped(f, x, area.bottom() - 2, width, &hints);
    register_region(
        app,
        app.vibe_code_project.search_area,
        MouseTarget::RemoteProject,
    );
    register_region(
        app,
        app.vibe_code_project.list_area,
        MouseTarget::RemoteProject,
    );
    if overflow {
        scrollbar::draw_large(
            app,
            f,
            MouseTarget::RemoteProject,
            Rect::new(area.right() - 4, area.y + 7, 1, visible as u16),
            total,
            visible,
            offset,
        );
    }
}
