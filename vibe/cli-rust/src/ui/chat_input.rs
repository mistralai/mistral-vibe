//! Chat input: the editable prompt with a `>` marker and top/bottom borders.

use ratatui::layout::{Alignment, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Padding, Paragraph};
use ratatui::Frame;

use super::composer_layout::ComposerLayout;
use super::theme;
use crate::agents;
use crate::app::{App, Status};
use crate::server::AgentSafety;

/// Border and border-title colors per agent safety (Python's
/// `SAFETY_BORDER_CLASSES` and the `#input-box` TCSS rules).
fn safety_colors(safety: AgentSafety) -> (Color, Color) {
    match safety {
        AgentSafety::Safe => (theme::success(), theme::success()),
        AgentSafety::Neutral => (theme::popup_border(), theme::muted()),
        AgentSafety::Destructive => (theme::warning(), theme::warning()),
        AgentSafety::Yolo => (theme::error(), theme::error()),
    }
}

pub fn draw(app: &App, f: &mut Frame, area: Rect) {
    // The chat input is usable as soon as the cached startup config is shown; it
    // buffers input locally while the app server is still loading (`Starting`),
    // so the caret and normal text show throughout. Only a failed start stays inert.
    let editable = !matches!(app.session.status, Status::Failed);
    // Queue selection locks the input read-only and hides the caret (ADR 0013),
    // but leaves the draft it holds styled like ordinary input.
    let selecting = app.queue.selected.is_some() && !app.queue.editing;
    let profile = agents::displayed(app);
    let bypass = agents::bypass_tool_permissions(app);
    let (border, title_color) = safety_colors(agents::indicator_safety(profile, bypass));
    let title = Line::from(vec![
        Span::styled(
            format!(" {} ", agents::indicator_agent_name(profile, bypass)),
            Style::default().fg(title_color),
        ),
        Span::styled("─", Style::default().fg(border)),
    ]);
    // Reserve one column on the right for the chat input content: Textual keeps a
    // 1-cell scrollbar gutter there (`scrollbar-size: 1`, bar hidden), so text
    // wraps and ends one column before the edge. The top/bottom borders still
    // span the full width; only the inner content is inset.
    let block = Block::default()
        .borders(Borders::TOP | Borders::BOTTOM)
        .border_style(Style::default().fg(border))
        .padding(Padding::right(1))
        .title(title)
        .title_alignment(Alignment::Right);

    // A slow agent switch, then recording, replace the prompt marker with their own glyph.
    let cursor = crate::chat_input::clamp_offset(&app.chat_input.input, app.chat_input.cursor);
    let marker = match agents::spinner_glyph(app).or_else(|| super::recording_indicator::glyph(app))
    {
        Some(glyph) => format!("{glyph} "),
        None => app.chat_input.mode.marker().to_owned(),
    };
    let body = app.chat_input.input.as_str();
    let body_off = cursor;
    let prompt_style = theme::text(theme::ORANGE).add_modifier(Modifier::BOLD);
    let input_style = if editable {
        theme::text(theme::foreground())
    } else {
        theme::muted_style()
    };
    let caret = (editable && !selecting && app.view.cursor_on).then_some(body_off);
    let sel =
        crate::chat_input::selection_range(&app.chat_input.input, cursor, app.chat_input.anchor);

    // A pasted block keeps its line breaks: the marker leads the first row and
    // continuation rows align under the text with a two-space gutter.
    let layout = ComposerLayout::new(body, &marker, body_off, area.width);
    let lines: Vec<Line> = layout
        .rows()
        .map(|row| {
            let text = row.text;
            let gutter = if row.index == 0 {
                Span::styled(marker.clone(), prompt_style)
            } else {
                Span::raw("  ")
            };
            let caret_col = caret.and_then(|_| layout.caret_in(row));
            // Selection overlap with this line, clamped to line-local columns.
            let line_sel = sel.and_then(|(lo, hi)| {
                (lo < row.end() && hi > row.start).then(|| {
                    let a = lo.max(row.start);
                    let b = hi.min(row.end());
                    (a - row.start, b - row.start)
                })
            });
            render_body_line(gutter, text, line_sel, caret_col, input_style)
        })
        .collect();
    // When the chat input has grown to its cap and the caret is below the visible
    // content rows, scroll so the caret line stays in view at the bottom.
    let content_h = area.height.saturating_sub(2) as usize;
    let max_scroll = layout.height().saturating_sub(content_h as u16);
    let scroll = app
        .chat_input
        .scroll
        .unwrap_or_else(|| layout.scroll(content_h))
        .min(max_scroll);
    let cursor_line_bg = theme::input_cursor_line_bg();
    let cursor_row = layout.caret_row();
    if caret.is_some()
        && cursor_line_bg != theme::background()
        && cursor_row >= scroll as usize
        && cursor_row < scroll as usize + content_h
    {
        let y = area.y + 1 + (cursor_row - scroll as usize) as u16;
        let row = Rect::new(area.x + 2, y, area.width.saturating_sub(2), 1);
        f.buffer_mut().set_style(
            row,
            Style::default().fg(theme::foreground()).bg(cursor_line_bg),
        );
    }
    let para = Paragraph::new(lines).scroll((scroll, 0)).block(block);
    f.render_widget(para, area);
    if scroll > 0 && area.height > 2 {
        f.buffer_mut()
            .set_string(area.x, area.y + 1, marker, prompt_style);
    }
}

fn composer_layout(app: &App, width: u16) -> ComposerLayout<'_> {
    let cursor = crate::chat_input::clamp_offset(&app.chat_input.input, app.chat_input.cursor);
    ComposerLayout::new(
        &app.chat_input.input,
        app.chat_input.mode.marker(),
        cursor,
        width,
    )
}

/// Count composer rows at `width`, including soft wraps.
pub fn content_height(app: &App, width: u16) -> u16 {
    composer_layout(app, width).height()
}

/// Current hidden composer viewport top row, clamped to the rendered content.
pub fn scroll(app: &App, width: u16, viewport_rows: usize) -> u16 {
    let layout = composer_layout(app, width);
    app.chat_input
        .scroll
        .unwrap_or_else(|| layout.scroll(viewport_rows))
        .min(layout.height().saturating_sub(viewport_rows as u16))
}

/// Cursor target one visible composer page above or below the current position.
pub fn page_cursor(app: &App, down: bool) -> usize {
    let layout = composer_layout(app, app.view.input_area.width);
    let rows = app.view.input_area.height.saturating_sub(2).max(1) as usize;
    layout.page_cursor(rows, down)
}

/// Move vertically through visual rows, returning the input offset and whether
/// another row existed in that direction.
pub fn vertical_cursor(app: &App, down: bool) -> (usize, bool) {
    composer_layout(app, app.view.input_area.width).vertical_offset(down)
}

/// Build one chat input row: base text, the selection range reversed, and the caret
/// cell as a solid block (trailing when the caret is at the line end). `sel` and
/// `caret` are byte columns within `text`. Adjacent chars sharing a style are
/// coalesced into one span.
fn render_body_line(
    gutter: Span<'static>,
    text: &str,
    sel: Option<(usize, usize)>,
    caret: Option<usize>,
    input_style: Style,
) -> Line<'static> {
    // The caret block matches Textual's TextArea: a solid $input-cursor-background
    // cell, or reverse-video for ansi themes (terminal-default background).
    let block = if theme::input_cursor_reverse() {
        Style::default()
            .fg(Color::Reset)
            .bg(theme::input_cursor_bg())
            .add_modifier(Modifier::REVERSED)
    } else {
        Style::default()
            .fg(theme::foreground())
            .bg(theme::input_cursor_bg())
    };
    let reversed = input_style.add_modifier(Modifier::REVERSED);
    let mut spans = vec![gutter];
    if text.is_empty() {
        // A word joiner is invisible but not Ratatui-wrappable whitespace.
        spans.push(Span::raw("\u{2060}"));
    }
    let mut buf = String::new();
    let mut buf_style: Option<Style> = None;
    for (b, ch) in text.char_indices() {
        let style = if caret == Some(b) {
            block
        } else if sel.is_some_and(|(lo, hi)| b >= lo && b < hi) {
            reversed
        } else {
            input_style
        };
        if buf_style != Some(style) {
            if let Some(s) = buf_style {
                spans.push(Span::styled(std::mem::take(&mut buf), s));
            }
            buf_style = Some(style);
        }
        buf.push(ch);
    }
    if let Some(s) = buf_style {
        spans.push(Span::styled(buf, s));
    }
    // Caret at (or past) the line end: a solid block on the trailing cell.
    if caret.is_some_and(|c| c >= text.len()) {
        spans.push(Span::styled(" ", block));
    }
    Line::from(spans)
}
