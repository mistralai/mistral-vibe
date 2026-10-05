//! Subagent status list: the bordered row list under the input box.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use super::theme::blend;
use crate::app::App;
use crate::mouse;
use crate::server::PublicChildSession;
use crate::subagents::{
    display_name, status_label, status_tone, StatusTone, MAIN_SESSION_ID, MAX_ROWS,
};

/// Python `#subagent-list` height: `max-height: 7` counts the whole widget,
/// border row included (Textual box model), so the view title row costs one.
/// The `.subagent-view` border exists only while the viewed child resolves.
pub fn height(app: &App) -> u16 {
    let rows = app.subagents.list.rows.len();
    if rows == 0 {
        return 0;
    }
    let viewing = usize::from(app.subagents.viewed_child().is_some());
    let cap = MAX_ROWS.saturating_sub(viewing);
    (viewing + (rows + 1).min(cap)) as u16
}

/// Row-render inputs snapshotted before the frame mutates the hitmap.
struct Rows {
    highlighted: usize,
    selected: Option<String>,
    mouse: Option<String>,
    focused: bool,
    free_scroll: Option<usize>,
    children: Vec<PublicChildSession>,
}

impl Rows {
    fn snapshot(app: &App) -> Self {
        let list = &app.subagents.list;
        Self {
            highlighted: list.highlighted,
            free_scroll: list.free_scroll,
            selected: list.selected_session_id.clone(),
            mouse: list.mouse_session_id.clone(),
            focused: list.focused,
            children: list.rows.clone(),
        }
    }

    /// Python `_sync_marker`: the mouse row wins, else the highlight while focused.
    fn marker_id(&self) -> Option<String> {
        if let Some(mouse) = &self.mouse {
            return Some(mouse.clone());
        }
        self.focused
            .then(|| self.row_id(self.highlighted))
            .flatten()
    }

    /// The id of a rendered row: Main or the child at `index - 1`.
    fn row_id(&self, index: usize) -> Option<String> {
        if index == 0 {
            return Some(MAIN_SESSION_ID.to_owned());
        }
        self.children.get(index - 1).map(|row| row.id.clone())
    }
}

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let rows = Rows::snapshot(app);
    if rows.children.is_empty() || area.height == 0 {
        app.subagents.list.row_areas.clear();
        return;
    }
    mouse::register_region(app, area, mouse::MouseTarget::SubagentList);
    app.subagents.list.area = area;
    let viewing = app.subagents.viewed_subagent_id.is_some();
    let mut top = area.y;
    // Python `.subagent-view`: a solid top border carrying the right-aligned
    // title; the border and its title drop together when the child is gone.
    if viewing {
        if let Some(name) = app
            .subagents
            .viewed_child()
            .map(|child| display_name(&child.name))
        {
            let title = format!(" Viewing {name} · read-only · Esc to return ");
            let width = area.width as usize;
            let padding = width.saturating_sub(title.chars().count());
            let padded = format!("{}{}", " ".repeat(padding), title);
            f.buffer_mut().set_stringn(
                area.x,
                top,
                &padded,
                width,
                Style::default().fg(theme::foreground()),
            );
            top += 1;
        }
    }
    let marker = rows.marker_id();
    let total = rows.children.len() + 1;
    let viewport = (area.height.saturating_sub(top - area.y) as usize).min(MAX_ROWS);
    // Textual `scroll_to_highlight` unless the wheel scrolled the viewport;
    // a free-scrolled offset wins until the highlight moves again.
    let base = rows
        .highlighted
        .saturating_sub(viewport.saturating_sub(1))
        .min(total.saturating_sub(viewport));
    let scroll = rows
        .free_scroll
        .unwrap_or(base)
        .min(total.saturating_sub(viewport));
    app.subagents.list.rendered_scroll = scroll;
    app.subagents.list.row_areas.clear();
    for index in scroll..total {
        if app.subagents.list.row_areas.len() >= viewport {
            break;
        }
        let row_area = Rect {
            y: top,
            width: area.width,
            height: 1,
            ..area
        };
        app.subagents.list.row_areas.push((row_area, index));
        let marked = marker.as_deref() == rows.row_id(index).as_deref();
        let (line, highlighted) = if index == 0 {
            (main_row(&rows, marked), rows.highlighted == 0)
        } else {
            let session = &rows.children[index - 1];
            (child_row(&rows, session, marked), rows.highlighted == index)
        };
        if highlighted {
            // The OptionList highlight background spans the whole row.
            f.buffer_mut()
                .set_style(row_area, Style::default().bg(theme::surface()));
        }
        let padded = Rect {
            x: area.x + 1,
            width: area.width.saturating_sub(2),
            ..row_area
        };
        f.render_widget(ratatui::widgets::Paragraph::new(line), padded);
        top += 1;
    }
}

/// Python `_main_row`: bold `Main conversation`, selected while in main chat.
fn main_row(rows: &Rows, marked: bool) -> Line<'static> {
    let style = row_style(rows, rows.selected.is_none(), rows.highlighted == 0, None);
    Line::from(vec![
        Span::styled(if marked { "> " } else { "  " }, style),
        Span::styled("Main conversation", style.add_modifier(Modifier::BOLD)),
    ])
}

/// Python `_child_row`: `AgentType (name) [status] · N tokens`.
fn child_row(rows: &Rows, session: &PublicChildSession, marked: bool) -> Line<'static> {
    let selected = rows.selected.as_deref() == Some(session.id.as_str());
    let highlighted = rows
        .children
        .iter()
        .position(|row| row.id == session.id)
        .is_some_and(|position| rows.highlighted == position + 1);
    let style = row_style(rows, selected, highlighted, Some(session.id.as_str()));
    let status_style = match status_tone(session.status) {
        StatusTone::Success => Style::default().fg(theme::success()),
        StatusTone::Warning => Style::default().fg(theme::warning()),
        StatusTone::Error => Style::default().fg(theme::error()),
        StatusTone::Muted => Style::default().fg(theme::muted()),
    };
    Line::from(vec![
        Span::styled(if marked { "> " } else { "  " }, style),
        Span::styled(
            display_name(&session.agent_type),
            style.add_modifier(Modifier::BOLD),
        ),
        Span::styled(format!(" ({}) ", display_name(&session.name)), style),
        Span::styled(format!("[{}]", status_label(session.status)), status_style),
        Span::styled(
            format!(
                " · {} tokens",
                crate::ui::bottom_bar::format_token_count(session.context_tokens())
            ),
            theme::muted_style(),
        ),
    ])
}

/// Python `_row` plus the OptionList hover/highlight TCSS rules: the highlighted
/// row is `$primary` on `$surface`; the selected row is `$primary`; the hovered
/// row is `$primary-darken-1`.
fn row_style(rows: &Rows, selected: bool, highlighted: bool, hovered: Option<&str>) -> Style {
    let style = Style::default().fg(theme::foreground());
    let hovered = hovered.is_some_and(|id| rows.mouse.as_deref() == Some(id));
    if highlighted {
        return style.fg(theme::primary()).bg(theme::surface());
    }
    if selected {
        return style.fg(theme::primary());
    }
    if hovered {
        // Textual's `$primary-darken-1`: 10% toward black.
        return style.fg(blend(
            theme::primary(),
            ratatui::style::Color::Rgb(0, 0, 0),
            0.1,
        ));
    }
    style
}
