//! The `/branch` result in the bottom-app slot: the command that resumes the copy.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Padding, Paragraph, Wrap};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::branch_app::resume_command;
use crate::hints;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let paragraph = Paragraph::new(lines(app))
        .style(theme::screen_style())
        .wrap(Wrap { trim: false });
    let height = paragraph.line_count(area.width.saturating_sub(4).max(1)) as u16 + 2;
    app.view.input_area = Rect::default();
    let kind = super::bottom_app::Kind::Branch;
    super::bottom_app::draw(app, f, area, height, kind, |_, f, area| {
        f.render_widget(
            paragraph.block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::muted_style())
                    .padding(Padding::horizontal(1)),
            ),
            area,
        );
    });
}

fn lines(app: &App) -> Vec<Line<'static>> {
    let title = theme::screen_style()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    vec![
        Line::styled("Session forked", title),
        Line::default(),
        Line::from("Copy and paste this command to resume it:"),
        Line::from(resume_command(&app.branch_app.session_id)),
        Line::default(),
        super::hint_line::line(&[hints::CLOSE]),
    ]
}
