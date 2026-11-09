//! The artifacts modal: one row per PR/MR (state dot, number, title, colored state) and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::pr_row::{self, Columns};
use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::artifacts;
use crate::artifacts_modal::{option_count, selected};
use crate::hints::{self, action, key, Hint};

/// The modal's footer hints: navigate, open the highlighted link, its check and fix keys when they apply, cancel.
fn hints(app: &App) -> Vec<Hint> {
    let selected = selected(app);
    let mut hints = vec![hints::NAVIGATE, (key::ENTER, action::OPEN)];
    if selected
        .and_then(crate::artifacts_modal::check_url)
        .is_some()
    {
        hints.push(("c", action::OPEN_CHECK));
    }
    if selected
        .and_then(crate::artifacts_modal::fix_prompt)
        .is_some()
    {
        hints.push(("f", action::FIX));
    }
    hints.push((key::ESC_CTRL_C, action::CANCEL));
    hints
}

/// Draw the whole screen with the modal replacing the input box; the chat
/// shrinks to make room for the bottom-app.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let visible = visible_rows(app, area.height);
    let kind = super::bottom_app::Kind::Artifacts;
    super::bottom_app::draw(
        app,
        f,
        area,
        visible as u16 + CHROME,
        kind,
        |app, f, area| draw_box(app, f, area, visible),
    );
}

/// Borders, title, the gap above the hint, and the hint around the rows.
const CHROME: u16 = 5;

/// Visible option rows: `min(count, 50vh)`.
fn visible_rows(app: &App, area_height: u16) -> usize {
    (area_height as usize / 2).min(option_count(app))
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, budget: usize) {
    if area.height < CHROME + 1 {
        return;
    }
    f.render_widget(Clear, area);
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let border = theme::border_style().bg(theme::background());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );
    let (bx, by, w, h) = (area.x, area.y, area.width, area.height);
    f.buffer_mut().set_string(
        bx + 2,
        by + 1,
        artifacts::modal_title(&app.session.artifacts),
        Style::default()
            .fg(theme::primary())
            .bg(theme::background())
            .add_modifier(Modifier::BOLD),
    );

    let total = option_count(app);
    let visible = usize::from(h - CHROME).min(budget.max(1));
    let top = by + 2;
    let list_area = Rect::new(bx + 1, top, w.saturating_sub(2), visible as u16);
    // Without overflow there is no scrollbar gutter, so the options span one
    // column further right (Textual `OptionList` option width).
    let has_scrollbar = total > visible;
    let gutter = if has_scrollbar { 7 } else { 6 };
    let options = Rect::new(bx + 3, top, w.saturating_sub(gutter), visible as u16);
    app.artifacts_modal.list_area = options;
    let offset = app.artifacts_modal.list.layout_rows(total, options);
    crate::mouse::register_region(app, list_area, crate::mouse::MouseTarget::ArtifactsModal);

    let prs: Vec<_> = app.session.artifacts.iter().collect();
    let columns = Columns::fit(&prs, options.width.into());
    let selected = app.artifacts_modal.list.selected();
    for (row, i) in (offset..total).take(visible).enumerate() {
        let row = Rect {
            y: top + row as u16,
            height: 1,
            ..options
        };
        if i == selected {
            list_cursor::paint(f, row);
        }
        let line = Line::from(pr_row::spans(prs[i], columns, i == selected));
        f.buffer_mut().set_line(row.x, row.y, &line, row.width);
    }

    if has_scrollbar {
        let bar = Rect::new(bx + w - 4, top, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::ArtifactsModal,
            bar,
            total as u16,
            visible as u16,
            offset as u16,
        );
    }

    hint_line::draw(f, bx + 2, by + h - 2, &hints(app));
}
