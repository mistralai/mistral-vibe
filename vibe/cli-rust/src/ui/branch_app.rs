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
    let paragraph = Paragraph::new(lines(app, area.width.saturating_sub(4)))
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

fn lines(app: &App, width: u16) -> Vec<Line<'static>> {
    let title = theme::screen_style()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let mut lines = vec![
        Line::styled("Session forked", title),
        Line::default(),
        Line::from("To continue in the forked session, run this command in another terminal:"),
        Line::from(resume_command(&app.branch_app.session_id)),
        Line::default(),
    ];
    lines.extend(super::hint_line::lines(&[hints::CLOSE], width));
    lines
}
