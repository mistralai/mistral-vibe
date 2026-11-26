//! `/goal` bottom app: the detail panel and the replace/clear confirmation.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{footer_gauge, goal_row, hint_line, list_cursor, theme};
use crate::app::App;
use crate::goal::{format, Confirm, Panel};
use crate::hints::{self, action, Hint};
use crate::mouse::MouseTarget;
use crate::server::{GoalStatus, PublicGoal};

/// Objective rows the detail panel shows before it ellipsizes the last one.
const MAX_OBJECTIVE_ROWS: usize = 8;
const BAR_CELLS: usize = 20;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if matches!(app.goal.panel, Some(Panel::Edit { .. })) {
        return super::goal_edit::draw(app, f, area);
    }
    let width = area.width.saturating_sub(4) as usize;
    let height = match &app.goal.panel {
        Some(Panel::Confirm(confirm)) => {
            9 + hint_line::extra(&confirm_hints(confirm.nested), area.width)
        }
        _ => app.goal.goal.as_ref().map_or(0, |goal| {
            let extra = hint_line::extra(&detail_hints(goal), area.width);
            objective_rows(goal, width).len() as u16 + 8 + extra
        }),
    };
    let kind = super::bottom_app::Kind::Goal;
    super::bottom_app::draw(app, f, area, height, kind, draw_box);
}

/// Paint the bordered box every goal panel uses, with its title; the content rectangle.
pub(super) fn frame(f: &mut Frame, area: Rect, title: Vec<Span<'static>>) -> Option<Rect> {
    if area.height < 4 || area.width < 8 {
        return None;
    }
    f.render_widget(Clear, area);
    let base = Style::default().bg(theme::background());
    f.buffer_mut().set_style(area, base);
    let border = base.fg(theme::popup_border());
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );
    let content = super::bottom_app::content(area);
    f.buffer_mut()
        .set_line(content.x, content.y, &Line::from(title), content.width);
    Some(content)
}

pub(super) fn heading(text: &str) -> Span<'static> {
    Span::styled(
        text.to_owned(),
        theme::text(theme::primary()).add_modifier(Modifier::BOLD),
    )
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    match &app.goal.panel {
        Some(Panel::Confirm(confirm)) => draw_confirm(app, f, area, confirm.nested),
        _ => draw_detail(app, f, area),
    }
}

fn draw_detail(app: &App, f: &mut Frame, area: Rect) {
    let Some(goal) = &app.goal.goal else {
        return;
    };
    let (glyph, glyph_style) = goal_row::icon(goal.status);
    let title = vec![
        Span::styled(format!("{glyph} "), glyph_style),
        heading("Goal"),
        Span::styled(
            format!(" · {}", format::status_label(goal.status)),
            theme::muted_style(),
        ),
    ];
    let Some(content) = frame(f, area, title) else {
        return;
    };
    let text = theme::text(theme::foreground());
    let hints = detail_hints(goal);
    let body = body(content, &hints);
    let mut y = content.y + 1;
    for row in objective_rows(goal, content.width as usize) {
        put_line(f, body, y, Line::styled(row, text));
        y += 1;
    }
    y += 1;
    let mut tokens = vec![label("Tokens"), Span::styled(format::usage(goal), text)];
    if let Some(budget) = goal.token_budget.filter(|budget| *budget > 0) {
        let percent = goal.tokens_used as f64 * 100.0 / budget as f64;
        let (bar, tone) = footer_gauge::bar(percent, BAR_CELLS);
        tokens.push(Span::raw("  "));
        tokens.extend(super::bottom_bar_parts::spans(vec![(bar, tone)]));
    }
    put_line(f, body, y, Line::from(tokens));
    let time = vec![
        label("Time"),
        Span::styled(format::duration(goal.time_used_seconds), text),
    ];
    put_line(f, body, y + 1, Line::from(time));
    draw_hints(f, content, &hints);
}

/// The content above the hint rows, which clips every other line in a short terminal.
pub(super) fn body(content: Rect, hints: &[Hint]) -> Rect {
    Rect {
        height: content
            .height
            .saturating_sub(hint_line::height(hints, content.width)),
        ..content
    }
}

/// The hints wrapped along the bottom of the content.
pub(super) fn draw_hints(f: &mut Frame, content: Rect, hints: &[Hint]) {
    let bottom = content.bottom().saturating_sub(1);
    hint_line::draw_above(f, content.x, bottom, content.width, hints);
}

fn confirm_hints(nested: bool) -> [Hint; 3] {
    let back = if nested { hints::BACK } else { hints::CANCEL };
    [hints::NAVIGATE, hints::SELECT, back]
}

/// Paint `line` at row `y` of `body`, or nothing when `y` falls below it.
pub(super) fn put_line(f: &mut Frame, body: Rect, y: u16, line: Line) {
    if y < body.bottom() {
        f.buffer_mut().set_line(body.x, y, &line, body.width);
    }
}

fn label(text: &str) -> Span<'static> {
    Span::styled(format!("{text:<8}"), theme::muted_style())
}

/// The objective wrapped to `width`, its last shown row ellipsized past the cap.
pub(crate) fn objective_rows(goal: &PublicGoal, width: usize) -> Vec<String> {
    let text = crate::utils::text::multi_line(&goal.objective);
    let mut rows = crate::utils::text::wrap_hard(&text, width.max(1));
    if rows.len() > MAX_OBJECTIVE_ROWS {
        rows.truncate(MAX_OBJECTIVE_ROWS);
        let last = format!("{} …", rows[MAX_OBJECTIVE_ROWS - 1]);
        rows[MAX_OBJECTIVE_ROWS - 1] = crate::utils::text::ellipsize(&last, width);
    }
    rows
}

/// `p` pauses a running goal and resumes a stopped one; it is hidden when neither applies.
pub(crate) fn detail_hints(goal: &PublicGoal) -> Vec<Hint> {
    let toggle = match goal.status {
        GoalStatus::Active => Some(("p", action::PAUSE)),
        status if format::resumable(status) => Some(("p", action::RESUME)),
        _ => None,
    };
    let mut list: Vec<Hint> = toggle.into_iter().collect();
    list.extend([("e", action::EDIT), ("c", action::CLEAR), hints::CLOSE]);
    list
}

fn draw_confirm(app: &mut App, f: &mut Frame, area: Rect, nested: bool) {
    let Some(Panel::Confirm(Confirm { kind, .. })) = &app.goal.panel else {
        return;
    };
    let (title, question, _) = kind.text();
    let labels = kind.options();
    let Some(content) = frame(f, area, vec![heading(title)]) else {
        return;
    };
    let footer = confirm_hints(nested);
    let body = body(content, &footer);
    let question = Line::styled(question, theme::text(theme::foreground()));
    put_line(f, body, content.y + 1, question);
    let options = Rect::new(
        content.x + 1,
        content.y + 3,
        content.width.saturating_sub(2),
        2,
    )
    .intersection(body);
    let scroll = app.goal.list.layout_rows(labels.len(), options);
    crate::mouse::register_region(app, options, MouseTarget::GoalConfirm);
    let shown = labels.iter().enumerate().skip(scroll);
    for (row_y, (index, label)) in (options.y..options.bottom()).zip(shown) {
        let row = Rect::new(options.x, row_y, options.width, 1);
        let highlighted = index == app.goal.list.selected();
        if highlighted {
            list_cursor::paint(f, row);
        }
        let (style, _) = list_cursor::styles(highlighted);
        f.buffer_mut()
            .set_stringn(row.x + 2, row.y, *label, row.width as usize, style);
    }
    draw_hints(f, content, &footer);
}
