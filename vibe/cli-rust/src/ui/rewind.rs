//! Rewind bottom-app: a bordered box with the message preview, the numbered
//! options of the current step, and a hint. Mirrors Python's `RewindApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::rewind::{options, Step};

/// Preview characters kept in the title (Python `_title_text`).
const PREVIEW_LIMIT: usize = 80;
const TITLE_PREFIX: &str = "Rewind to: ";

/// One rendered row: `(text, style)` runs starting at the content column.
type Row = Vec<(String, Style)>;

/// Draw the whole screen with the panel replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let rows = rows(app);
    let box_height = (rows.len() as u16 + 2).min(area.height);
    let kind = super::bottom_app::Kind::Rewind;
    super::bottom_app::draw(app, f, area, box_height, kind, |_, f, area| {
        draw_box(f, area, &rows)
    });
}

fn draw_box(f: &mut Frame, area: Rect, rows: &[Row]) {
    if area.height < 3 {
        return;
    }
    f.render_widget(Clear, area);
    let bg = theme::background();
    f.buffer_mut().set_style(area, Style::default().bg(bg));
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_style(Style::default().fg(theme::popup_border()).bg(bg)),
        area,
    );

    // One border column plus the `padding: 0 1` of `#rewind-app`.
    let x = area.x + 2;
    let visible = area.height.saturating_sub(2) as usize;
    for (index, row) in rows.iter().take(visible).enumerate() {
        let y = area.y + 1 + index as u16;
        if list_cursor::is_bar(row) {
            let bar = Rect::new(x, y, area.width.saturating_sub(4), 1);
            f.buffer_mut().set_style(bar, list_cursor::style());
        }
        let mut cursor = x;
        for (text, style) in row {
            let style = style.bg.map_or(style.bg(bg), |_| *style);
            f.buffer_mut().set_string(cursor, y, text, style);
            cursor += text.chars().count() as u16;
        }
    }
}

/// Every content row, in Textual compose order: title, gap, options, gap, hint.
fn rows(app: &App) -> Vec<Row> {
    let title = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    // A collapsed paste keeps its placeholder, set apart from the title color.
    let placeholder = title.fg(theme::secondary());
    let mut rows: Vec<Row> = app
        .rewind
        .preview
        .truncated(TITLE_PREFIX, PREVIEW_LIMIT)
        .lines()
        .map(|segments| {
            segments
                .into_iter()
                .map(|(text, is_placeholder)| {
                    let style = if is_placeholder { placeholder } else { title };
                    (text.to_owned(), style)
                })
                .collect()
        })
        .collect();
    rows.push(Vec::new());
    for (index, label) in options(app).iter().enumerate() {
        let style = if index == app.rewind.selected {
            list_cursor::style()
        } else {
            Style::default().fg(theme::foreground())
        };
        rows.push(vec![(format!("  {}. {label}", index + 1), style)]);
    }
    rows.push(Vec::new());
    rows.push(help(app));
    rows
}

/// The hint line for the current step.
fn help(app: &App) -> Row {
    const PICK: Hint = (key::NAV, action::PICK_OPTION);
    const CONFIRM: Hint = (key::ENTER, action::CONFIRM);
    const QUIT: Hint = ("q", action::QUIT);
    let list: &[Hint] = match app.rewind.step {
        Step::Persistence => &[PICK, CONFIRM, hints::BACK, QUIT],
        Step::Action => &[
            (key::LEFT_ESC, action::PREVIOUS),
            (key::RIGHT, action::NEXT),
            PICK,
            CONFIRM,
            QUIT,
        ],
    };
    super::hint_line::styled(list)
}
