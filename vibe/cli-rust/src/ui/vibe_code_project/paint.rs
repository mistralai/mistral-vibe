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

pub(crate) fn field(f: &mut Frame, area: Rect, field: &mut Field, focused: bool, cursor_on: bool) {
    let base = theme::text(theme::foreground()).bg(theme::background());
    let selected = crate::chat_input::selection_range(&field.text, field.cursor, field.anchor);
    let caret = theme::fixed::input_caret(base);
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
    let (base, _) = crate::ui::list_cursor::styles(highlighted);
    let (fg, bg) = (base.fg.unwrap_or_default(), base.bg.unwrap_or_default());
    let dimmed = dim(base);
    if highlighted {
        crate::ui::list_cursor::paint(f, area);
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
    let gap = if highlighted {
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

/// Rich's `red`/`cyan`, as Textual renders named colors.
fn action_color(red: bool) -> Color {
    use theme::fixed::Named;
    theme::fixed::named(if red { Named::Red } else { Named::Cyan })
}
