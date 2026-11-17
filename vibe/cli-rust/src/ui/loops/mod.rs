//! The `/loop` manager: the rows (title, recurrence, next run) with the highlighted loop's instructions under or beside them past a rule, or the add form.

mod details;
mod form;
mod list;

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::Frame;

use super::browser_box::{self, SIDE_BY_SIDE};
use super::theme;
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::loops::{rows, FormField};
use crate::utils::text::ellipsize;

/// Borders, the title and its blank row, the blank row above the footer, and the footer.
const CHROME: u16 = 6;
const EMPTY: &str = "No scheduled loops.";

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let layout = Layout::fit(app, area.width, area.height / 2);
    let kind = super::bottom_app::Kind::Loops;
    super::bottom_app::draw(
        app,
        f,
        area,
        layout.height() + CHROME,
        kind,
        |app, f, area| draw_box(app, f, area, layout),
    );
}

/// Where the instructions sit, and how many rows the list and they take.
#[derive(Clone, Copy)]
struct Layout {
    /// The instructions sit beside the rows, on a box wide enough for both.
    beside: bool,
    rows: u16,
    /// Instructions lines; 0 on the form or an empty list.
    details: u16,
    error: bool,
}

impl Layout {
    /// The rows and the instructions in `budget` lines (`50vh`): the pane fits the longest instructions, so the box keeps its height as the highlight moves.
    fn fit(app: &App, width: u16, budget: u16) -> Self {
        let error = app.loops.error.is_some();
        let budget = budget.saturating_sub(u16::from(error)).max(2);
        let loops = rows(app).len() as u16;
        if app.loops.form.is_some() || loops == 0 {
            let rows = match &app.loops.form {
                Some(form) => form::height(form, width.saturating_sub(4)).min(budget),
                None => 1,
            };
            return Self {
                beside: false,
                rows,
                details: 0,
                error,
            };
        }
        let tallest = |width: u16| {
            let heights = rows(app).into_iter().map(|row| details::height(row, width));
            heights.max().unwrap_or(1).max(1)
        };
        if width >= SIDE_BY_SIDE {
            let pane = (width - width / 2).saturating_sub(4);
            let rows = loops.max(tallest(pane)).min(budget);
            return Self {
                beside: true,
                rows,
                details: rows,
                error,
            };
        }
        let room = budget.saturating_sub(1 + loops.min(budget / 2));
        let details = tallest(width.saturating_sub(4)).min(room).max(1);
        let rows = loops.min(budget.saturating_sub(details + 1)).max(1);
        Self {
            beside: false,
            rows,
            details,
            error,
        }
    }

    fn height(self) -> u16 {
        let stacked = match self.beside || self.details == 0 {
            true => self.rows,
            false => self.rows + 1 + self.details,
        };
        stacked + u16::from(self.error)
    }
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect, layout: Layout) {
    if area.height < CHROME + 1 {
        return;
    }
    let title = if app.loops.form.is_some() {
        "New loop"
    } else {
        "Loops"
    };
    browser_box::draw_frame(f, area, title);
    let body = Rect::new(
        area.x + 1,
        area.y + 3,
        area.width.saturating_sub(2),
        layout.height(),
    );
    let footer = match app.loops.form.is_some() {
        true => {
            form::draw(app, f, inner(body, 0, layout.rows));
            form_hints(app)
        }
        false => {
            draw_list_and_details(app, f, area, body, layout);
            list_hints(app)
        }
    };
    if let Some(error) = &app.loops.error {
        let row = inner(body, layout.height() - 1, 1);
        let style = Style::default()
            .fg(theme::text_error())
            .bg(theme::background());
        let text = ellipsize(error, row.width.into());
        f.buffer_mut().set_string(row.x, row.y, text, style);
    }
    browser_box::draw_help(f, area, &footer);
}

fn draw_list_and_details(app: &mut App, f: &mut Frame, area: Rect, body: Rect, layout: Layout) {
    if rows(app).is_empty() {
        let muted = theme::muted_style().bg(theme::background());
        let row = inner(body, 0, 1);
        f.buffer_mut().set_string(row.x, row.y, EMPTY, muted);
        return;
    }
    if layout.beside {
        let (left, right) = browser_box::halves(area);
        browser_box::split_selection(app, (left, right));
        list::draw(
            app,
            f,
            inner(
                Rect {
                    width: left.width - 1,
                    ..body
                },
                0,
                layout.rows,
            ),
        );
        draw_rule(f, Rect::new(right.x, body.y, 1, layout.rows));
        let pane = Rect::new(
            right.x + 2,
            body.y,
            right.width.saturating_sub(4),
            layout.details,
        );
        details::draw(app, f, pane);
        return;
    }
    list::draw(app, f, inner(body, 0, layout.rows));
    draw_rule(f, inner(body, layout.rows, 1));
    details::draw(app, f, inner(body, layout.rows + 1, layout.details));
}

/// The rule between the rows and the instructions: down a one-column `area`, else across it.
fn draw_rule(f: &mut Frame, area: Rect) {
    let style = theme::border_style().bg(theme::background());
    let buf = f.buffer_mut();
    match area.width {
        1 => (area.y..area.bottom()).for_each(|y| buf.set_string(area.x, y, "│", style)),
        _ => buf.set_string(area.x, area.y, "─".repeat(area.width.into()), style),
    };
}

/// `height` rows from `from` lines into `body`, inside the box padding.
fn inner(body: Rect, from: u16, height: u16) -> Rect {
    Rect::new(
        body.x + 1,
        body.y + from,
        body.width.saturating_sub(2),
        height,
    )
}

fn list_hints(app: &App) -> Vec<Hint> {
    let mut hints = Vec::new();
    if !rows(app).is_empty() {
        hints.extend([hints::NAVIGATE, ("d", action::DELETE)]);
    }
    hints.extend([("n", action::NEW), hints::CLOSE]);
    hints
}

fn form_hints(app: &App) -> Vec<Hint> {
    let mut hints = vec![
        (key::ARROWS_TAB, action::NAVIGATE),
        (key::ENTER, action::CREATE),
    ];
    let instructions = app
        .loops
        .form
        .as_ref()
        .is_some_and(|form| form.focused_field() == FormField::Instructions);
    if instructions {
        hints.push((key::CTRL_J, action::NEWLINE));
    }
    hints.push(hints::BACK);
    hints
}
