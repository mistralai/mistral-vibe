//! The goal line above the input, shown only while it offers the resume choice.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::theme;
use crate::app::App;
use crate::goal::format;
use crate::server::GoalStatus;

/// The resume choice's text after the goal, when the closed session suspended it.
pub const RESUME_QUESTION: &str = "paused when the session closed";

/// One line while the resume choice waits; otherwise the rail carries the goal.
pub fn height(app: &App) -> u16 {
    u16::from(app.goal.goal.is_some() && app.goal.resume_prompt)
}

/// The status glyph and its color.
pub fn icon(status: GoalStatus) -> (&'static str, Style) {
    match status {
        GoalStatus::Active => ("▶", theme::text(theme::primary())),
        GoalStatus::Paused => ("‖", theme::muted_style()),
        GoalStatus::Complete => ("✓", theme::text(theme::success())),
        _ => ("!", theme::text(theme::warning())),
    }
}

/// Paint the goal line and publish it as a selectable region.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    if height(app) == 0 || area.width < 1 || area.height < 1 {
        return;
    }
    let row = Rect { height: 1, ..area };
    f.render_widget(Paragraph::new(line(app, row.width)), row);
    super::selection::goal_region(app, f, row);
}

/// `▶ Goal <objective> · <stats>`, or the resume choice in place of the stats.
fn line(app: &App, width: u16) -> Line<'static> {
    let Some(goal) = &app.goal.goal else {
        return Line::default();
    };
    let (glyph, glyph_style) = icon(goal.status);
    let tail = match app.goal.resume_prompt {
        true => resume_spans(),
        false => vec![Span::styled(
            format!(" · {}", format::row_detail(goal)),
            theme::dim(theme::foreground()),
        )],
    };
    let tail_width: usize = tail.iter().map(Span::width).sum();
    let head = format!("{glyph} Goal ");
    let objective_width = usize::from(width)
        .saturating_sub(head.width() + tail_width)
        .max(1);
    let objective = crate::utils::text::single_line(&goal.objective);
    let objective = crate::utils::text::ellipsize(&objective, objective_width);
    let mut spans = vec![
        Span::styled(format!("{glyph} "), glyph_style),
        Span::styled(
            "Goal ",
            theme::text(theme::foreground()).add_modifier(Modifier::BOLD),
        ),
        Span::styled(objective, theme::muted_style()),
    ];
    spans.extend(tail);
    Line::from(spans)
}

/// ` · paused when the session closed · 1. Resume  2. Leave paused`.
fn resume_spans() -> Vec<Span<'static>> {
    let key = theme::text(theme::primary());
    let text = theme::text(theme::foreground());
    vec![
        Span::styled(format!(" · {RESUME_QUESTION} · "), text),
        Span::styled("1", key),
        Span::styled(". Resume  ", text),
        Span::styled("2", key),
        Span::styled(". Leave paused", text),
    ]
}
