//! Clipped project rows and compact input fields.

use ratatui::{
    layout::Rect,
    style::{Color, Modifier, Style},
    text::{Line, Span},
    Frame,
};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::ui::theme;
use crate::vibe_code_project::{items::Item, Field, State};

pub(super) fn text(f: &mut Frame, x: u16, y: u16, width: u16, value: &str, style: Style) {
    f.buffer_mut()
        .set_stringn(x, y, value, width as usize, style.bg(theme::background()));
}

pub(super) fn repository(f: &mut Frame, x: u16, y: u16, width: u16, repo: &str, creating: bool) {
    let base = theme::text(theme::foreground());
    let label = if creating { base } else { dim(base) };
    f.buffer_mut().set_line(
        x,
        y,
        &Line::from(vec![
            Span::styled("Repository: ", label),
            Span::styled(repo, base),
        ]),
        width,
    );
}

pub(super) fn help(f: &mut Frame, x: u16, y: u16, width: u16, hints: &[(&str, &str)]) {
    let key = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    let spans: Vec<_> = hints
        .iter()
        .flat_map(|(k, label)| {
            [
                Span::styled(*k, key),
                Span::styled(*label, theme::muted_style()),
            ]
        })
        .collect();
    f.buffer_mut().set_line(x, y, &Line::from(spans), width);
}

pub(super) fn field(f: &mut Frame, area: Rect, field: &mut Field, focused: bool, cursor_on: bool) {
    let base = theme::text(theme::foreground()).bg(theme::background());
    let selected = crate::chat_input::selection_range(&field.text, field.cursor, field.anchor);
    let caret = if theme::is_ansi() {
        base.fg(Color::Black).bg(Color::Gray)
    } else {
        base.fg(theme::background())
            .bg(theme::active().input_cursor_bg)
    };
    field.scroll_to_cursor(area.width as usize);
    let skip = field.scroll;
    let mut used = 0;
    let mut spans = Vec::new();
    for (index, ch) in field
        .text
        .char_indices()
        .chain(std::iter::once((field.text.len(), ' ')))
    {
        let width = ch.to_string().width();
        used += width;
        if used <= skip {
            continue;
        }
        if used > skip + area.width as usize {
            break;
        }
        let style = if focused && cursor_on && index == field.cursor {
            caret
        } else if focused && selected.is_some_and(|(a, b)| index >= a && index < b) {
            base.fg(theme::selection_fg())
                .bg(theme::active().input_selection_bg)
        } else {
            base
        };
        spans.push(Span::styled(ch.to_string(), style));
    }
    f.buffer_mut()
        .set_line(area.x, area.y, &Line::from(spans), area.width);
}

pub(super) fn item(f: &mut Frame, area: Rect, state: &State, index: usize, name_width: usize) {
    let Some(view) = &state.view else { return };
    let item = &state.items[index];
    let highlighted = index == state.selected && item.selectable();
    let selected = highlighted && !state.search_focused;
    let bg = if selected {
        theme::block_cursor_bg()
    } else if highlighted && !theme::is_ansi() {
        theme::blend(theme::background(), theme::primary(), 76.0 / 255.0)
    } else {
        theme::background()
    };
    let fg = if selected {
        theme::block_cursor_fg()
    } else {
        theme::foreground()
    };
    let mut base = theme::text(fg).bg(bg);
    if selected {
        base = base.add_modifier(Modifier::BOLD);
    }
    let dimmed = dim(base);
    if highlighted {
        f.buffer_mut().set_style(area, base);
    }
    if let Item::Section(label) = item {
        f.buffer_mut().set_stringn(
            area.x,
            area.y,
            label,
            area.width as usize,
            dim(theme::text(if theme::is_ansi() {
                fg
            } else {
                theme::blend(bg, theme::auto_contrast(), 0.38)
            })
            .bg(bg)),
        );
        return;
    }
    let (count, status, name_style) = match item {
        Item::Project {
            index: project,
            rank,
        } => {
            let count = view.state.projects[*project].repositories.len();
            let status = match rank {
                0 => "Currently linked",
                1 => "Exact match found",
                _ => "Working repository found",
            };
            let recommended = state
                .items
                .iter()
                .position(|i| matches!(i, Item::Project { .. }))
                == Some(index);
            (
                format!("{count} {}", if count == 1 { "repo" } else { "repos" }),
                status,
                if recommended {
                    base.add_modifier(Modifier::BOLD)
                } else {
                    base
                },
            )
        }
        Item::Create { recommended, .. } => (
            String::new(),
            if *recommended {
                "recommended"
            } else {
                "repo-linked project"
            },
            if *recommended {
                base.fg(action_color(false))
            } else {
                base
            },
        ),
        Item::Unlink => (String::new(), "", base.fg(action_color(true))),
        _ => (String::new(), "", base),
    };
    let name = fit_to_width(item.label(view), name_width);
    let name_padding = name_width.saturating_sub(name.width());
    let gap = if selected {
        dimmed
    } else {
        Style::default().bg(bg)
    };
    let spans = vec![
        Span::styled(format!("{name}{}", " ".repeat(name_padding)), name_style),
        Span::styled("   ", gap),
        Span::styled(format!("{count:<8}"), dimmed),
        Span::styled("   ", gap),
        Span::styled(status, dimmed),
    ];
    f.buffer_mut()
        .set_line(area.x, area.y, &Line::from(spans), area.width);
}

fn fit_to_width(value: &str, width: usize) -> String {
    if value.width() <= width {
        return value.to_owned();
    }
    let mut result = String::new();
    let content_width = width.saturating_sub(1);
    let mut used = 0;
    for ch in value.chars() {
        let char_width = ch.width().unwrap_or(0);
        if used + char_width > content_width {
            break;
        }
        result.push(ch);
        used += char_width;
    }
    result.push('…');
    result
}

pub(super) fn dim(style: Style) -> Style {
    if theme::is_ansi() {
        style.add_modifier(Modifier::DIM)
    } else {
        style.fg(theme::blend(
            style.bg.unwrap_or(theme::background()),
            style.fg.unwrap_or(theme::foreground()),
            theme::DIM_FACTOR,
        ))
    }
}

// Rich's named colors use Textual's Monokai/Alabaster terminal palette.
fn action_color(red: bool) -> Color {
    match (theme::is_ansi(), theme::is_dark(), red) {
        (true, _, true) => Color::Red,
        (true, _, false) => Color::Cyan,
        (false, true, true) => Color::Rgb(244, 0, 95),
        (false, true, false) => Color::Rgb(88, 209, 235),
        (false, false, true) => Color::Rgb(170, 55, 49),
        (false, false, false) => Color::Rgb(0, 131, 178),
    }
}
