//! Nested config editor with disjoint, content-sized regions.

mod layout;
mod persistence;

use ratatui::layout::{Margin, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Clear, Paragraph};
use ratatui::Frame;
use unicode_segmentation::UnicodeSegmentation;

use super::{composer_layout::ComposerLayout, scrollbar, theme};
use crate::app::App;
use crate::config_edit::{self, ConfigEdit};
pub(super) use layout::draw_too_small;
use layout::{height, paragraph, Layout};

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let Some(edit) = app.config_screen.edit.as_mut() else {
        return;
    };
    edit.choice_regions.clear();
    let values = config_edit::choices(&edit.field);
    let selected = config_edit::choice_index(edit, &values);
    let choices: Vec<_> = values
        .iter()
        .map(|choice| config_edit::choice_label(&edit.field, choice))
        .collect();
    let Some(layout) = Layout::new(edit, &choices, area) else {
        draw_too_small(
            app,
            f,
            area,
            "Enlarge terminal to edit this setting. Esc Cancel",
        );
        return;
    };
    let bg = theme::surface();
    f.render_widget(Clear, layout.modal);
    f.buffer_mut()
        .set_style(layout.modal, Style::default().bg(bg));
    f.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Rounded)
            .border_style(Style::default().fg(theme::primary()).bg(bg))
            .title(Line::from(vec![
                Span::raw("─ "),
                Span::styled(
                    edit.field.name.as_str(),
                    Style::default().add_modifier(Modifier::BOLD),
                ),
                Span::raw(" "),
            ])),
        layout.modal,
    );
    let mut description = edit.field.description.clone();
    if choices.is_empty() && config_edit::is_multiline(&edit.field) {
        if !description.is_empty() {
            description.push('\n');
        }
        description.push_str(if edit.field.kind == "list" {
            "One item per line."
        } else {
            "Edit as JSON."
        });
    }
    f.render_widget(
        paragraph(&description).style(theme::dim(theme::muted())),
        Rect::new(
            layout.main.x,
            layout.main.y,
            layout.main.width,
            layout.description_height,
        ),
    );
    let editor = layout.editor();
    let scrollbar = if choices.is_empty() {
        input(f, editor, edit);
        None
    } else {
        draw_choices(f, editor, edit, &choices, selected)
    };
    persistence::inspector(f, layout.side, &edit.field);
    f.render_widget(
        Paragraph::new("─".repeat(usize::from(layout.targets.width)))
            .style(Style::default().fg(theme::muted())),
        Rect::new(
            layout.targets.x,
            layout.targets.y - 2,
            layout.targets.width,
            1,
        ),
    );
    persistence::targets(f, layout.targets, &app.config_screen.targets, edit.target);
    help(
        f,
        layout.help,
        !choices.is_empty(),
        config_edit::is_multiline(&edit.field),
    );
    if let Some(error) = &edit.error {
        f.render_widget(
            Paragraph::new(error.as_str()).style(Style::default().fg(theme::error())),
            Rect::new(
                layout.targets.x,
                layout.targets.y - 3,
                layout.targets.width,
                1,
            ),
        );
    }
    crate::mouse::register_region(app, layout.modal, crate::mouse::MouseTarget::Blocked);
    if !choices.is_empty() {
        crate::mouse::register_region(app, editor, crate::mouse::MouseTarget::ConfigEditor);
    }
    if let Some((track, total, offset)) = scrollbar {
        scrollbar::draw_large(
            app,
            f,
            crate::mouse::MouseTarget::ConfigEditor,
            track,
            total,
            usize::from(editor.height),
            offset,
        );
    }
}

fn input(f: &mut Frame, mut area: Rect, edit: &mut ConfigEdit) {
    if !config_edit::is_multiline(&edit.field) {
        area.height = area.height.min(3);
    }
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(theme::primary()));
    let inner = layout::input_inner(area);
    edit.input_width = Some(inner.width);
    f.render_widget(block, area);
    let cursor_style = Style::default()
        .fg(theme::block_cursor_fg())
        .bg(theme::block_cursor_bg());
    let layout = ComposerLayout::hard_wrapped(&edit.draft, edit.cursor, inner.width);
    let offset = layout.viewport_top(edit.scroll.unwrap_or(0), usize::from(inner.height));
    edit.scroll = Some(offset);
    let rows = layout
        .rows()
        .skip(offset)
        .take(usize::from(inner.height))
        .map(|row| {
            let Some(at) = layout.caret_in(row) else {
                return Line::raw(row.text);
            };
            let len = row.text[at..].graphemes(true).next().map_or(0, str::len);
            Line::from(vec![
                Span::raw(row.text[..at].to_owned()),
                Span::styled(
                    if len == 0 {
                        " ".to_owned()
                    } else {
                        row.text[at..at + len].to_owned()
                    },
                    cursor_style,
                ),
                Span::raw(row.text[at + len..].to_owned()),
            ])
        })
        .collect::<Vec<_>>();
    f.render_widget(
        Paragraph::new(rows).style(Style::default().fg(theme::foreground())),
        inner,
    );
}

pub fn vertical_cursor(edit: &ConfigEdit, down: bool) -> usize {
    let Some(width) = edit.input_width else {
        return edit.cursor;
    };
    ComposerLayout::hard_wrapped(&edit.draft, edit.cursor, width)
        .vertical_offset(down)
        .0
}

fn draw_choices(
    f: &mut Frame,
    area: Rect,
    edit: &mut ConfigEdit,
    choices: &[String],
    selected: usize,
) -> Option<(Rect, usize, usize)> {
    let mut content = area.inner(Margin::new(1, 0));
    let visible = usize::from(area.height);
    if choices
        .iter()
        .map(|label| usize::from(height(label, content.width)))
        .sum::<usize>()
        > visible
    {
        content.width = content.width.saturating_sub(2);
    }
    let heights: Vec<usize> = choices
        .iter()
        .map(|label| usize::from(height(label, content.width)))
        .collect();
    let total: usize = heights.iter().sum();
    let selected_start: usize = heights.iter().take(selected).sum();
    let selected_height = heights.get(selected).copied().unwrap_or(1);
    let offset = (selected_start + selected_height)
        .saturating_sub(visible)
        .min(selected_start);
    let offset = edit
        .scroll
        .unwrap_or(offset)
        .min(total.saturating_sub(visible));
    edit.scroll = edit.scroll.map(|_| offset);
    let mut row = 0;
    for (index, (label, count)) in choices.iter().zip(heights).enumerate() {
        let end = row + count;
        let start = row.max(offset);
        let bottom = end.min(offset + visible);
        if start < bottom {
            let rect = Rect::new(
                content.x,
                content.y + (start - offset) as u16,
                content.width,
                (bottom - start) as u16,
            );
            let style = if index == selected {
                Style::default()
                    .fg(theme::block_cursor_fg())
                    .bg(theme::block_cursor_bg())
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
                    .fg(theme::foreground())
                    .bg(theme::surface())
            };
            if index == selected {
                f.buffer_mut().set_style(
                    Rect::new(rect.x, rect.y, area.width - 2, rect.height),
                    style,
                );
            }
            f.render_widget(
                paragraph(label)
                    .scroll(((start - row) as u16, 0))
                    .style(style),
                rect,
            );
            edit.choice_regions.push((rect, index));
        }
        row = end;
        if row >= offset + visible {
            break;
        }
    }
    (total > visible).then_some((
        Rect::new(area.right() - 2, area.y, 1, area.height),
        total,
        offset,
    ))
}

fn help(f: &mut Frame, area: Rect, choices: bool, multiline: bool) {
    let save = match (choices, multiline) {
        (true, _) => "Enter Select",
        (false, true) => "Ctrl+S Save",
        _ => "Enter Save",
    };
    let mut hints = Vec::new();
    if choices {
        hints.push(("↑↓/jk", "Navigate"));
    }
    let (key, action) = save.split_once(' ').unwrap();
    hints.extend([(key, action), ("Esc", "Cancel"), ("Tab", "Change Layer")]);
    if area.width < 60 {
        hints.retain(|(key, _)| *key != "↑↓/jk");
        hints.last_mut().unwrap().1 = "Layer";
    }
    let mut spans = Vec::new();
    for (index, (key, action)) in hints.iter().enumerate() {
        if index > 0 {
            spans.push(Span::raw("  "));
        }
        let style = if choices {
            Style::default()
                .fg(theme::primary())
                .add_modifier(Modifier::BOLD)
        } else {
            theme::dim(theme::muted())
        };
        spans.push(Span::styled(*key, style));
        spans.push(Span::styled(
            format!(" {action}"),
            theme::dim(theme::muted()),
        ));
    }
    f.render_widget(Paragraph::new(Line::from(spans)), area);
}
