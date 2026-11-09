//! Subagent status list: the bordered row list under the input box.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::{hint_line, list_cursor, scrollbar, theme};
use crate::app::App;
use crate::hints::action;
use crate::mouse;
use crate::server::PublicChildSession;
use crate::subagents::{
    display_name, page_rows, status_label, status_tone, StatusTone, MAIN_SESSION_ID, MAX_ROWS,
};

/// List height cap: the 7 rows count the whole widget, border row included,
/// so the view title row costs one. The view border exists only while the
/// viewed child resolves.
pub fn height(app: &App) -> u16 {
    if !app.subagents.list.shown {
        return 0;
    }
    let rows = app.subagents.list.rows.len();
    let summary = usize::from(app.subagents.list.waiting_count > 0);
    let viewing = usize::from(app.subagents.viewed_child().is_some());
    (viewing + summary + (rows + 1).min(page_rows(app))) as u16
}

/// Row-render inputs snapshotted before the frame mutates the hitmap.
struct Rows {
    highlighted: usize,
    selected: Option<String>,
    mouse: Option<String>,
    focused: bool,
    children: Vec<PublicChildSession>,
}

impl Rows {
    fn snapshot(app: &App) -> Self {
        let list = &app.subagents.list;
        Self {
            highlighted: list.options.selected(),
            selected: list.selected_session_id.clone(),
            mouse: list.mouse_session_id.clone(),
            focused: list.focused,
            children: list.rows.clone(),
        }
    }

    /// The mouse row wins, else the highlight while focused.
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
    let (waiting, show_waiting) = (
        app.subagents.list.waiting_count,
        app.subagents.list.show_waiting,
    );
    if !app.subagents.list.shown || area.height == 0 {
        app.subagents.list.options.layout(&[], Rect::default());
        return;
    }
    mouse::register_region(app, area, mouse::MouseTarget::SubagentList);
    app.subagents.list.area = area;
    let viewing = app.subagents.viewed_subagent_id.is_some();
    let mut top = area.y;
    // The child view: a solid top border carrying the right-aligned
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
    let summary = u16::from(waiting > 0);
    let viewport = (area.height.saturating_sub(top - area.y + summary) as usize).min(MAX_ROWS);
    let list_area = Rect {
        y: top,
        height: viewport as u16,
        ..area
    };
    let scroll = app.subagents.list.options.layout_rows(total, list_area);
    for index in (scroll..total).take(viewport) {
        let row_area = Rect {
            y: top,
            height: 1,
            ..area
        };
        let marked = marker.as_deref() == rows.row_id(index).as_deref();
        let line = match index {
            0 => main_row(&rows, marked),
            _ => child_row(&rows, &rows.children[index - 1], marked),
        };
        if marked {
            f.buffer_mut().set_style(row_area, list_cursor::style());
        }
        let padded = Rect {
            x: area.x + 1,
            width: area.width.saturating_sub(2),
            ..row_area
        };
        f.render_widget(ratatui::widgets::Paragraph::new(line), padded);
        top += 1;
    }
    if total > viewport && area.width > 0 {
        let bar = Rect {
            x: area.right() - 1,
            width: 1,
            ..list_area
        };
        let target = mouse::MouseTarget::SubagentList;
        scrollbar::draw_large(app, f, target, bar, total, viewport, scroll);
    }
    if waiting > 0 && top < area.bottom() {
        let row = Rect {
            x: area.x + 1,
            y: top,
            width: area.width.saturating_sub(2),
            height: 1,
        };
        let line = waiting_summary(waiting, show_waiting, rows.focused);
        f.render_widget(ratatui::widgets::Paragraph::new(line), row);
    }
}

/// The last row: `N subagents waiting`, plus `· a to show`/`· a to hide` while the list owns the keys.
fn waiting_summary(waiting: usize, show_waiting: bool, focused: bool) -> Line<'static> {
    let muted = hint_line::action_style();
    let noun = if waiting == 1 {
        "subagent"
    } else {
        "subagents"
    };
    let mut spans = vec![
        Span::raw(list_cursor::BLANK),
        Span::styled(format!("{waiting} {noun} waiting"), muted),
    ];
    if focused {
        let action = match show_waiting {
            true => action::HIDE,
            false => action::SHOW,
        };
        spans.push(Span::styled(" · ", muted));
        spans.extend(hint_line::inline_spans(&[("a", action)]));
    }
    Line::from(spans)
}

/// The main row: bold `Main conversation`, current while in main chat.
fn main_row(rows: &Rows, marked: bool) -> Line<'static> {
    let style = row_style(marked);
    Line::from(vec![
        current_marker(rows.selected.is_none(), marked),
        Span::styled("Main conversation", style.add_modifier(Modifier::BOLD)),
    ])
}

/// The green `›` on the session being viewed (the list's current value).
fn current_marker(current: bool, marked: bool) -> Span<'static> {
    let style = match marked {
        true => list_cursor::style(),
        false => Style::default().fg(list_cursor::current_color()),
    };
    Span::styled(list_cursor::marker(current), style)
}

/// A child row: `AgentType (name) [status] · N tokens`.
fn child_row(rows: &Rows, session: &PublicChildSession, marked: bool) -> Line<'static> {
    let current = rows.selected.as_deref() == Some(session.id.as_str());
    let style = row_style(marked);
    let status_color = match status_tone(session.status) {
        StatusTone::Success => theme::success(),
        StatusTone::Warning => theme::warning(),
        StatusTone::Error => theme::error(),
        StatusTone::Muted => theme::muted(),
    };
    let muted = match marked {
        true => list_cursor::styles(true).1,
        false => theme::muted_style(),
    };
    Line::from(vec![
        current_marker(current, marked),
        Span::styled(
            display_name(&session.agent_type),
            style.add_modifier(Modifier::BOLD),
        ),
        Span::styled(format!(" ({}) ", display_name(&session.name)), style),
        Span::styled(
            format!("[{}]", status_label(session.status)),
            list_cursor::accent(style, status_color),
        ),
        Span::styled(
            format!(
                " · {} tokens",
                crate::utils::text::format_compact_count(session.context_tokens())
            ),
            muted,
        ),
    ])
}

/// A row's text: the list-cursor bar under keyboard focus or the mouse, plain otherwise.
fn row_style(marked: bool) -> Style {
    match marked {
        true => list_cursor::style(),
        false => Style::default().fg(theme::foreground()),
    }
}
