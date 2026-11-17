//! `/goal edit` bottom app: the objective field, the budget field, an error line and hints.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::goal_panel::{body, frame, heading, put_line};
use super::tab_cells::cells;
use super::{hint_line, theme};
use crate::app::App;
use crate::goal::edit::OBJECTIVE_ROWS;
use crate::goal::{EditForm, Panel};
use crate::hints::{action, key, Hint};

const BUDGET_PLACEHOLDER: &str = "No budget (e.g. 200k, 1.5m)";

fn form(app: &mut App) -> Option<&mut EditForm> {
    match app.goal.panel.as_mut() {
        Some(Panel::Edit { form, .. }) => Some(form),
        _ => None,
    }
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let width = area.width.saturating_sub(6).max(1);
    let Some(form) = form(app) else {
        return;
    };
    form.width = width;
    let rows = (form.layout().height() as usize).clamp(1, OBJECTIVE_ROWS) as u16;
    let kind = super::bottom_app::Kind::Goal;
    super::bottom_app::draw(app, f, area, rows + 9, kind, draw_box);
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    let cursor_on = app.view.cursor_on;
    let nested = matches!(app.goal.panel, Some(Panel::Edit { nested: true, .. }));
    let Some(form) = form(app) else {
        return;
    };
    let Some(content) = frame(f, area, vec![heading("Edit goal")]) else {
        return;
    };
    let label = theme::muted_style();
    let x = content.x + 2;
    let body = body(content);
    put_line(f, body, content.y + 1, Line::styled("Objective", label));
    let rows = (form.layout().height() as usize).clamp(1, OBJECTIVE_ROWS);
    let objective = Rect::new(x, content.y + 2, form.width, rows as u16).intersection(body);
    draw_objective(f, form, objective, cursor_on);
    let budget_y = content.y + 3 + rows as u16;
    put_line(f, body, budget_y, Line::styled("Token budget", label));
    let field = Rect::new(x, budget_y + 1, content.width.saturating_sub(2), 1);
    if field.y < body.bottom() {
        bar(f, content.x, field.y, form.budget_focused);
        draw_budget(f, field, form, cursor_on);
    }
    if let Some(error) = form.error {
        put_line(
            f,
            body,
            field.y + 1,
            Line::styled(error, theme::text(theme::error())),
        );
    }
    let back = if nested {
        crate::hints::BACK
    } else {
        crate::hints::CANCEL
    };
    let hints: [Hint; 4] = [
        (key::TAB, action::SWITCH_FIELD),
        (key::ENTER, action::SAVE),
        (key::CTRL_J, action::NEWLINE),
        back,
    ];
    hint_line::draw_clipped(f, content.x, body.bottom(), content.width, &hints);
}

fn draw_budget(f: &mut Frame, field: Rect, form: &mut EditForm, cursor_on: bool) {
    let focused = form.budget_focused;
    let budget = &mut form.budget;
    super::proxy_setup::draw_field_or_placeholder(
        f,
        field,
        budget,
        BUDGET_PLACEHOLDER,
        focused,
        cursor_on,
    );
}

/// The focus bar left of a field.
fn bar(f: &mut Frame, x: u16, y: u16, focused: bool) {
    let color = if focused {
        theme::primary()
    } else {
        theme::popup_border()
    };
    f.buffer_mut().set_string(x, y, "▎", theme::text(color));
}

/// The wrapped objective, scrolled to keep the caret in view.
fn draw_objective(f: &mut Frame, form: &mut EditForm, area: Rect, cursor_on: bool) {
    if area.is_empty() {
        return;
    }
    let focused = !form.budget_focused;
    bar(f, area.x - 2, area.y, focused);
    let layout = form.layout();
    let top = layout.viewport_top(form.scroll, area.height as usize);
    let text = theme::text(theme::foreground()).bg(theme::background());
    let caret = theme::fixed::input_caret(text);
    for (index, row) in layout
        .rows()
        .skip(top)
        .take(area.height as usize)
        .enumerate()
    {
        let y = area.y + index as u16;
        if index > 0 {
            bar(f, area.x - 2, y, focused);
        }
        let at = (focused && cursor_on)
            .then(|| layout.caret_in(row))
            .flatten();
        let mut spans = Vec::new();
        let mut caret_drawn = false;
        for cell in cells(row.text, row.column) {
            let on_caret = at == Some(cell.byte);
            caret_drawn |= on_caret;
            spans.push(Span::styled(
                cell.shown(),
                if on_caret { caret } else { text },
            ));
        }
        if at.is_some() && !caret_drawn {
            spans.push(Span::styled(" ", caret));
        }
        f.buffer_mut()
            .set_line(area.x, y, &Line::from(spans), area.width);
    }
    form.scroll = top;
}
