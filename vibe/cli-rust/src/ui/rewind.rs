//! Rewind bottom-app: a bordered box with the message preview, the numbered
//! options of the current step, and a hint. Mirrors Python's `RewindApp`.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::rewind::{options, Step};

/// Preview characters kept in the title (Python `_title_text`).
const PREVIEW_LIMIT: usize = 80;

/// One rendered row: `(text, style)` runs starting at the content column.
type Row = Vec<(String, Style)>;

/// Draw the whole screen with the panel replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let rows = rows(app);
    let box_height = (rows.len() as u16 + 2).min(area.height);
    let chunks = super::bottom_app_chunks(app, area, loading_height, box_height);

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Rewind);
    draw_box(f, chunks[2], &rows);
    super::todo::draw_row(app, f, chunks[4]);
    bottom_bar::draw(app, f, chunks[3]);
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
        let mut cursor = x;
        for (text, style) in row {
            f.buffer_mut()
                .set_string(cursor, area.y + 1 + index as u16, text, style.bg(bg));
            cursor += text.chars().count() as u16;
        }
    }
}

/// Every content row, in Textual compose order: title, gap, options, gap, hint.
fn rows(app: &App) -> Vec<Row> {
    let title = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let mut rows: Vec<Row> = title_text(&app.rewind.preview)
        .lines()
        .map(|line| vec![(line.to_owned(), title)])
        .collect();
    rows.push(Vec::new());
    for (index, label) in options(app).iter().enumerate() {
        let focused = index == app.rewind.selected;
        let style = if focused {
            Style::default()
                .fg(theme::primary())
                .add_modifier(Modifier::BOLD)
        } else {
            Style::default().fg(theme::foreground())
        };
        let cursor = if focused { "› " } else { "  " };
        rows.push(vec![(format!("{cursor}{}. {label}", index + 1), style)]);
    }
    rows.push(Vec::new());
    rows.push(help(app));
    rows
}

fn title_text(preview: &str) -> String {
    let preview: String = preview.chars().take(PREVIEW_LIMIT).collect();
    format!("Rewind to: {preview}")
}

/// The hint line: keys in bold $primary, labels in $text-muted.
fn help(app: &App) -> Row {
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let label = theme::dim(theme::text_muted());
    let hints: &[(&str, &str)] = match app.rewind.step {
        Step::Persistence => &[
            ("↑↓/jk", " pick option  "),
            ("Enter", " confirm  "),
            ("Esc", " back  "),
            ("q", " quit"),
        ],
        Step::Action => &[
            ("←/Esc", " previous  "),
            ("→", " next  "),
            ("↑↓/jk", " pick option  "),
            ("Enter", " confirm  "),
            ("q", " quit"),
        ],
    };
    hints
        .iter()
        .flat_map(|(k, text)| [((*k).to_owned(), key), ((*text).to_owned(), label)])
        .collect()
}
