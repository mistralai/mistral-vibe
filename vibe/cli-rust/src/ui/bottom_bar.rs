//! Bottom bar: working directory + PID on the left, token budget on the right.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::selection::{cells, Granularity};
use crate::utils::paths::collapse_home;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::BottomBar);
    // Working directory from `App`; `-` if unresolved.
    let cwd = crate::replay::footer_cwd(
        app.session
            .cwd
            .as_deref()
            .map(collapse_home)
            .unwrap_or_else(|| "-".into()),
    );
    // While a Ctrl+C quit is pending the path is replaced by the confirm hint.
    let pid = crate::replay::footer_pid_label(format!(" [PID {}]", std::process::id()));
    let muted = theme::muted_style();
    let left = if app.quit_confirm_active() {
        Line::from(vec![
            Span::styled("Press ", muted),
            Span::styled("Ctrl+C", Style::default().fg(theme::primary())),
            Span::styled(" again to quit", muted),
            Span::styled(pid, muted),
        ])
    } else {
        Line::from(Span::styled(format!("{cwd}{pid}"), muted))
    };
    // Token budget, empty until the first `session/statsUpdated` stats arrive.
    let right = Line::from(Span::styled(
        format_context(app.session.tokens),
        theme::muted_style(),
    ));
    // `#spacer` `width: 1fr`: the right context sits at the edge, but the left
    // path keeps its natural width, so an overflowing bar clips the context.
    let left_width = (left.width() as u16).min(area.width);
    // The `1fr` spacer never collapses below one cell.
    let spaced = (area.x + left_width + 1).min(area.right());
    let right_x = area
        .right()
        .saturating_sub(right.width() as u16)
        .max(spaced);
    f.render_widget(
        Paragraph::new(left),
        Rect {
            width: left_width,
            ..area
        },
    );
    f.render_widget(
        Paragraph::new(right),
        Rect {
            x: right_x,
            width: area.right() - right_x,
            ..area
        },
    );
    paint_selection(app, f, area);
}

/// Highlight the bottom-bar selection and, on a pending release, copy its text.
fn paint_selection(app: &mut App, f: &mut Frame, area: Rect) {
    let Some((anchor, head)) = app
        .selection
        .bottom_bar
        .as_ref()
        .map(|sel| (sel.anchor, sel.head))
    else {
        return;
    };
    let lo = anchor.min(head).max(area.x);
    let hi = anchor.max(head).min(area.right().saturating_sub(1));
    if lo > hi {
        app.selection.bottom_bar = None;
        return;
    }
    // The chain picks char/word/paragraph; snap the span the same way the
    // transcript does, once the painted row is known.
    let (x0, x1) = selected_columns(f.buffer_mut(), area, lo, hi, app.selection.granularity);
    // Cache the text every paint so a copy key can read it with autocopy off.
    let text = read_row(f.buffer_mut(), area.y, x0, x1);
    if let Some(sel) = app.selection.bottom_bar.as_mut() {
        sel.text = text;
    }
    honor_pending_copy(app);
    let style = Style::default()
        .fg(theme::selection_fg())
        .bg(theme::selection_bg());
    let buf = f.buffer_mut();
    for x in x0..=x1 {
        if let Some(cell) = buf.cell_mut((x, area.y)) {
            cell.set_style(cell.style().patch(style));
        }
    }
}

/// Copy a bottom-bar selection whose release armed a deferred copy, using the
/// text cached at paint time so no frame buffer is read. Called from paint and
/// before a new press supersedes the selection, so the copy is never dropped.
pub fn honor_pending_copy(app: &mut App) {
    let Some(sel) = app.selection.bottom_bar.as_ref() else {
        return;
    };
    if !sel.pending_copy {
        return;
    }
    let text = sel.text.clone();
    crate::ui::notice::copy_if_autocopy(app, &text);
    if let Some(sel) = app.selection.bottom_bar.as_mut() {
        sel.pending_copy = false;
    }
}

/// Widen the raw column range `[lo, hi]` to the click granularity, reading the
/// painted row: a word snaps to its `\w+` run, a paragraph to the whole line.
pub fn selected_columns(
    buf: &Buffer,
    area: Rect,
    lo: u16,
    hi: u16,
    granularity: Granularity,
) -> (u16, u16) {
    let y = area.y;
    let right = area.right().saturating_sub(1);
    match granularity {
        Granularity::Char => (lo, hi),
        Granularity::Word => (
            cells::word_start(buf, lo, area.x, y),
            cells::word_end(buf, hi, right, y),
        ),
        Granularity::Paragraph => {
            let x0 = cells::skip_blanks_right(buf, area.x, right, y);
            (x0, cells::trim_blanks_left(buf, right, x0, y))
        }
    }
}

/// Read cells `[x0, x1]` on screen row `y` back as text, trimming trailing blanks.
fn read_row(buf: &Buffer, y: u16, x0: u16, x1: u16) -> String {
    let row: String = (x0..=x1)
        .filter_map(|x| buf.cell((x, y)).map(|cell| cell.symbol()))
        .collect();
    row.trim_end().to_string()
}

/// Render the context budget `(current, max)`; empty while `max == 0`.
fn format_context((current, max): (u64, u64)) -> String {
    if max == 0 {
        return String::new();
    }
    let ratio = (current as f64 / max as f64).min(1.0);
    let pct = (ratio * 100.0).round() as u64;
    format!(
        "{}/{} tokens ({pct}%)",
        format_token_count(current),
        format_token_count(max),
    )
}

/// Abbreviate a token count: `x.yM`, `Nk`, or the raw number below one thousand.
fn format_token_count(tokens: u64) -> String {
    if tokens >= 1_000_000 {
        format!("{:.1}M", tokens as f64 / 1_000_000.0)
    } else if tokens >= 1_000 {
        format!("{}k", tokens / 1_000)
    } else {
        tokens.to_string()
    }
}
