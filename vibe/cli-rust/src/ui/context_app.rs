//! `/context` bottom app: a headline, one full-width gauge of the context window
//! (two rows, `▌` half cells for twice the resolution, the buffer hatched on the
//! track, `▼ auto-compact` over the threshold), then a left-aligned legend.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Clear, Paragraph};
use ratatui::Frame;

use super::context_bar::{self, percent, Segment};
use super::{bottom_app, hint_line, theme};
use crate::app::App;
use crate::hints;
use crate::server::{ContextBreakdown, ContextCategoryId};
use crate::utils::text::format_compact_count;

const CLOSE_HINTS: &[hints::Hint] = &[hints::CLOSE];
/// Box rows: border, headline, blank, marker, two gauge rows, blank, six legend
/// rows, blank, hint, border.
const BOX_HEIGHT: u16 = 16;
/// Legend label column: swatch, space and the padded label, up to the percent.
const LABEL_WIDTH: u16 = 27;
/// The percent is right-aligned in this many cells (`100%`), so the `%` signs line up.
const PERCENT_WIDTH: u16 = 4;
const LEGEND: [ContextCategoryId; 4] = [
    ContextCategoryId::System,
    ContextCategoryId::Tools,
    ContextCategoryId::Skills,
    ContextCategoryId::Conversation,
];

/// Draw the whole screen with the context app replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let height = BOX_HEIGHT.min(area.height.saturating_sub(2)).max(5);
    bottom_app::draw(app, f, area, height, bottom_app::Kind::Context, draw_box);
}

fn bg() -> Style {
    Style::default().bg(theme::background())
}

/// Secondary text: the muted color, plus SGR dim on ANSI themes where blends don't apply.
fn muted() -> Style {
    theme::muted_style().bg(theme::background())
}

/// Primary text, never inheriting a dim from a previous paint.
fn strong() -> Style {
    theme::text(theme::foreground())
        .bg(theme::background())
        .add_modifier(Modifier::BOLD)
}

fn color_of(segment: Segment) -> Color {
    let [system, tools, skills, conversation, other] = theme::context_palette();
    match segment {
        Segment::Category(ContextCategoryId::System) => system,
        Segment::Category(ContextCategoryId::Tools) => tools,
        Segment::Category(ContextCategoryId::Skills) => skills,
        Segment::Category(ContextCategoryId::Conversation) => conversation,
        Segment::Category(ContextCategoryId::Unknown) => other,
        Segment::Unsplit => theme::muted(),
        Segment::Free => theme::context_track(),
        Segment::Buffer => theme::context_buffer(),
    }
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    f.render_widget(Clear, area);
    f.buffer_mut().set_style(area, bg());
    let border = bg().fg(theme::popup_border());
    let block = Block::default().borders(Borders::ALL).border_style(border);
    f.render_widget(block, area);
    let inner = Rect {
        x: area.x + 2,
        y: area.y + 1,
        width: area.width.saturating_sub(4),
        height: area.height.saturating_sub(2),
    };
    if inner.height < 3 || inner.width < 10 {
        return;
    }
    let hint_y = inner.bottom() - 1;
    hint_line::draw_clipped(f, inner.x, hint_y, inner.width, CLOSE_HINTS);
    let Some(b) = app.context_app.breakdown.clone() else {
        let message = app
            .context_app
            .error
            .as_deref()
            .unwrap_or("Reading context usage…");
        f.buffer_mut()
            .set_stringn(inner.x, inner.y, message, inner.width as usize, muted());
        return;
    };
    let mut y = inner.y;
    line(f, inner, y, headline(&b));
    y += 2;
    if y + 3 <= hint_y {
        draw_gauge(
            f,
            &b,
            Rect {
                y,
                height: 3,
                ..inner
            },
        );
        y += 4;
    }
    for row in legend(&b) {
        if y + 1 >= hint_y {
            break;
        }
        line(f, inner, y, row);
        y += 1;
    }
}

fn line(f: &mut Frame, area: Rect, y: u16, content: Line<'static>) {
    let row = Rect {
        y,
        height: 1,
        ..area
    };
    f.render_widget(Paragraph::new(content).style(bg()), row);
}

/// `Context  21%  42.3k of 200k tokens`, or a note before the first model call.
fn headline(b: &ContextBreakdown) -> Line<'static> {
    let title = Span::styled(
        "Context",
        bg().fg(theme::primary()).add_modifier(Modifier::BOLD),
    );
    let window = format_compact_count(b.context_window);
    if !b.measured || b.context_window == 0 {
        let note = format!("   {window} token window · usage appears after the first response");
        return Line::from(vec![title, Span::styled(note, muted())]);
    }
    let used = b.used_tokens.min(b.context_window);
    let pct = format!("   {}%", percent(used, b.context_window));
    let detail = format!(
        " ({} of {window} tokens)",
        format_compact_count(b.used_tokens)
    );
    Line::from(vec![
        title,
        Span::styled(pct, strong()),
        Span::styled(detail, muted()),
    ])
}

/// The marker row, then two identical bar rows: each cell holds two slots,
/// drawn as `▌` (fg = left slot, bg = right slot).
fn draw_gauge(f: &mut Frame, b: &ContextBreakdown, area: Rect) {
    let width = area.width as usize;
    let slots = context_bar::slots(b, width * 2);
    let buffer_start = context_bar::buffer_start(&slots);
    let buf = f.buffer_mut();
    for cell in 0..width {
        let (left, right) = (slots[cell * 2], slots[cell * 2 + 1]);
        let x = area.x + cell as u16;
        for row in 1..area.height {
            let style = bg().fg(color_of(left)).bg(color_of(right));
            let symbol = match (left, right) {
                (Segment::Buffer, Segment::Buffer) => "░",
                _ if left == right => "█",
                _ => "▌",
            };
            let style = match symbol {
                "░" => style.bg(theme::context_track()),
                "█" => style.bg(theme::background()),
                _ => style,
            };
            buf.set_string(x, area.y + row, symbol, style);
        }
    }
    let Some(start) = buffer_start else { return };
    let pct = percent(b.compaction_threshold, b.context_window);
    let at = area.x + start as u16;
    let after = format!("▼ auto-compact at {pct}%");
    let width = after.chars().count() as u16;
    if at + width <= area.right() {
        buf.set_string(at, area.y, after, muted());
        return;
    }
    // Clipped from its start, so the `▼` always lands on the threshold cell.
    let before = format!("auto-compact at {pct}% ▼");
    let x = (at + 1).saturating_sub(width).max(area.x);
    let kept = (at + 1 - x) as usize;
    let skipped = before.chars().count() - kept;
    let clipped: String = before.chars().skip(skipped).collect();
    buf.set_string(x, area.y, clipped, muted());
}

/// One row per category, then free space and the buffer: swatch, label, and the
/// value (`pct%` bright, tokens muted).
fn legend(b: &ContextBreakdown) -> Vec<Line<'static>> {
    let measured = b.measured && b.context_window > 0;
    let others = b.categories.iter().filter_map(|category| {
        let id = category.id.unwrap_or(ContextCategoryId::Unknown);
        (!LEGEND.contains(&id)).then_some((
            Segment::Category(id),
            id.label(),
            category.estimated_tokens,
        ))
    });
    let used = if measured {
        b.used_tokens.min(b.context_window)
    } else {
        0
    };
    let known: Vec<_> = if measured && b.categories.is_empty() {
        vec![(Segment::Unsplit, "In use", used)]
    } else {
        LEGEND
            .iter()
            .map(|id| {
                let tokens = b
                    .categories
                    .iter()
                    .find(|c| c.id == Some(*id))
                    .map_or(0, |c| c.estimated_tokens);
                (Segment::Category(*id), id.label(), tokens)
            })
            .collect()
    };
    let threshold = b.compaction_threshold.min(b.context_window);
    let rest = [
        (Segment::Free, "Free space", threshold.saturating_sub(used)),
        (
            Segment::Buffer,
            "Compaction buffer",
            b.context_window.saturating_sub(threshold.max(used)),
        ),
    ];
    known
        .into_iter()
        .chain(others)
        .map(|(segment, label, tokens)| {
            (segment, label, if measured { tokens } else { 0 }, measured)
        })
        .chain(rest.map(|(segment, label, tokens)| (segment, label, tokens, true)))
        .map(|(segment, label, tokens, shown)| legend_row(b, segment, label, tokens, shown))
        .collect()
}

fn legend_row(
    b: &ContextBreakdown,
    segment: Segment,
    label: &str,
    tokens: u64,
    shown: bool,
) -> Line<'static> {
    let swatch = match segment {
        Segment::Buffer => "░░",
        _ => "██",
    };
    let swatch_style = match segment {
        Segment::Buffer => bg().fg(color_of(segment)).bg(theme::context_track()),
        _ => bg().fg(color_of(segment)),
    };
    let label = format!(
        " {label:<width$}",
        width = (LABEL_WIDTH - 3 - PERCENT_WIDTH) as usize
    );
    let mut spans = vec![
        Span::styled(swatch, swatch_style),
        Span::styled(
            label,
            theme::text(theme::foreground()).bg(theme::background()),
        ),
    ];
    if !shown {
        spans.push(Span::styled(
            format!("{:>w$}", "—", w = PERCENT_WIDTH as usize),
            muted(),
        ));
        return Line::from(spans);
    }
    let pct = percent(tokens, b.context_window);
    let pct_text = if tokens > 0 && pct == 0 {
        "<1%".to_owned()
    } else {
        format!("{pct}%")
    };
    let pct_text = format!("{pct_text:>w$}", w = PERCENT_WIDTH as usize);
    spans.push(Span::styled(pct_text, strong()));
    spans.push(Span::styled(
        format!(" ({} tokens)", format_compact_count(tokens)),
        muted(),
    ));
    Line::from(spans)
}
