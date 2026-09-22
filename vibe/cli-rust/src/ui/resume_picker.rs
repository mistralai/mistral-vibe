//! `/resume` picker view.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::{bottom_bar, loading, scrollbar, theme, transcript};
use crate::app::App;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));
    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let height = (app.resume_picker.sessions.len().max(1) as u16 + 6).min(area.height / 2 + 6);
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(loading_height),
        Constraint::Length(height),
        Constraint::Length(1),
    ])
    .split(area);
    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    draw_box(app, f, chunks[2]);
    bottom_bar::draw(app, f, chunks[3]);
}

fn draw_box(app: &mut App, f: &mut Frame, area: Rect) {
    if area.height < 5 {
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
    let title = Style::default().fg(theme::secondary()).bg(bg);
    let cwd = app.resume_picker.cwd.as_deref().unwrap_or("this folder");
    f.buffer_mut()
        .set_string(area.x + 2, area.y + 1, "local", title);
    f.buffer_mut()
        .set_string(area.x + 8, area.y + 1, cwd, theme::muted_style().bg(bg));
    let visible = area.height.saturating_sub(6) as usize;
    reconcile_scroll(app, visible);
    let total = app.resume_picker.sessions.len();
    let has_scrollbar = total > visible;
    let row_right = area
        .x
        .saturating_add(area.width.saturating_sub(if has_scrollbar { 4 } else { 3 }));
    crate::mouse::register_region(
        app,
        Rect::new(
            area.x + 1,
            area.y + 3,
            area.width.saturating_sub(2),
            visible as u16,
        ),
        crate::mouse::MouseTarget::ResumePicker,
    );
    if app.resume_picker.sessions.is_empty() {
        f.buffer_mut().set_string(
            area.x + 3,
            area.y + 3,
            "Loading sessions...",
            theme::muted_style().bg(bg),
        );
    }
    for (row, session) in app
        .resume_picker
        .sessions
        .iter()
        .skip(app.resume_picker.scroll)
        .take(visible)
        .enumerate()
    {
        let y = area.y + 3 + row as u16;
        let selected = app.resume_picker.scroll + row == app.resume_picker.selected;
        let row_bg = if selected {
            theme::block_cursor_bg()
        } else {
            bg
        };
        let text = if session.title.is_empty() {
            &session.preview
        } else {
            &session.title
        };
        let time = format!("{:10}", relative_time(session.updated_at));
        let id = &session.id[..session.id.len().min(8)];
        let delete_message = if app.resume_picker.deleting.as_deref() == Some(&session.id) {
            Some("Deleting...")
        } else if app.resume_picker.delete_confirm.as_deref() == Some(&session.id) {
            if app.session.session_id.as_deref() == Some(&session.id) {
                Some("Can't delete current session")
            } else {
                Some("Press d again to delete")
            }
        } else {
            None
        };
        if selected {
            let gutter = if has_scrollbar { 7 } else { 6 };
            f.buffer_mut().set_style(
                Rect::new(area.x + 3, y, area.width.saturating_sub(gutter), 1),
                Style::default().bg(row_bg),
            );
        }
        let style = Style::default()
            .fg(if selected {
                theme::block_cursor_fg()
            } else {
                theme::foreground()
            })
            .bg(row_bg);
        let muted = if selected {
            style.add_modifier(Modifier::DIM | Modifier::BOLD)
        } else {
            style.add_modifier(Modifier::DIM)
        };
        let text_style = if selected {
            style.add_modifier(Modifier::BOLD)
        } else {
            style
        };
        f.buffer_mut().set_string(area.x + 3, y, time, muted);
        f.buffer_mut().set_string(area.x + 15, y, id, muted);
        let message = delete_message.unwrap_or(text);
        let message_x = area.x + 25;
        let message_width = row_right.saturating_sub(message_x) as usize;
        let message = crate::utils::text::ellipsize(message, message_width);
        f.buffer_mut()
            .set_stringn(message_x, y, &message, message_width, text_style);
        // Python styles the `d` of "Press d again to delete" as a shortcut.
        if let Some(offset) = message.strip_suffix(" again to delete").map(str::len) {
            let shortcut_x = message_x + offset as u16 - 1;
            if shortcut_x < row_right {
                f.buffer_mut()
                    .set_string(shortcut_x, y, "d", text_style.fg(theme::primary()));
            }
        }
    }
    if has_scrollbar {
        let bar = Rect::new(area.x + area.width - 4, area.y + 3, 1, visible as u16);
        scrollbar::draw(
            app,
            f,
            crate::mouse::MouseTarget::ResumePicker,
            bar,
            total as u16,
            visible as u16,
            app.resume_picker.scroll as u16,
        );
    }
    let key = Style::default()
        .fg(theme::primary())
        .bg(bg)
        .add_modifier(Modifier::BOLD);
    let label = theme::muted_style().bg(bg);
    f.buffer_mut()
        .set_string(area.x + 2, area.y + area.height - 2, "↑↓/jk", key);
    f.buffer_mut()
        .set_string(area.x + 7, area.y + area.height - 2, " Navigate  ", label);
    f.buffer_mut()
        .set_string(area.x + 18, area.y + area.height - 2, "Enter", key);
    f.buffer_mut()
        .set_string(area.x + 23, area.y + area.height - 2, " Select  ", label);
    f.buffer_mut()
        .set_string(area.x + 32, area.y + area.height - 2, "d", key);
    f.buffer_mut()
        .set_string(area.x + 33, area.y + area.height - 2, " Delete ", label);
    f.buffer_mut()
        .set_string(area.x + 42, area.y + area.height - 2, "Esc", key);
    f.buffer_mut()
        .set_string(area.x + 45, area.y + area.height - 2, " Cancel", label);
}

fn relative_time(timestamp: u64) -> String {
    let now = crate::utils::replay_now_ms().unwrap_or_else(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |value| value.as_millis() as u64)
    });
    let seconds = now.saturating_sub(timestamp) / 1_000;
    match seconds {
        0..=59 => "just now".into(),
        60..=3_599 => format!("{}m ago", seconds / 60),
        3_600..=86_399 => format!("{}h ago", seconds / 3_600),
        86_400..=604_799 => format!("{}d ago", seconds / 86_400),
        _ => format!("{}w ago", seconds / 604_800),
    }
}

fn reconcile_scroll(app: &mut App, visible: usize) {
    if visible == 0 {
        return;
    }
    let max_scroll = app.resume_picker.sessions.len().saturating_sub(visible);
    if app.resume_picker.free_scroll {
        app.resume_picker.scroll = app.resume_picker.scroll.min(max_scroll);
        return;
    }
    let selected = app.resume_picker.selected;
    if selected < app.resume_picker.scroll {
        app.resume_picker.scroll = selected;
    }
    if selected >= app.resume_picker.scroll + visible {
        app.resume_picker.scroll = selected + 1 - visible;
    }
    app.resume_picker.scroll = app.resume_picker.scroll.min(max_scroll);
}
