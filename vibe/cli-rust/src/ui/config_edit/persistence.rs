//! Bounded layer inspector and persistence controls.

use ratatui::layout::{Margin, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Paragraph};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::super::theme;
use crate::config::{origin_label, ConfigField};
use crate::utils::text::ellipsize;

pub(super) fn inspector(f: &mut Frame, area: Rect, field: &ConfigField) {
    if area.is_empty() {
        return;
    }
    let block = Block::default()
        .borders(Borders::LEFT)
        .border_style(Style::default().fg(theme::muted()));
    let content = block.inner(area).inner(Margin::new(1, 0));
    f.render_widget(block, area);
    let muted = theme::dim(theme::muted());
    let mut lines = vec![
        Line::styled("WHERE IT'S SET", muted.add_modifier(Modifier::BOLD)),
        Line::default(),
    ];
    let label_width = field
        .layers
        .iter()
        .map(|(layer, _)| origin_label(layer).width())
        .max()
        .unwrap_or(0);
    let budget = usize::from(content.width).saturating_sub(label_width + 4);
    for (index, (layer, value)) in field.layers.iter().enumerate() {
        let active = index == 0;
        let style = if active {
            Style::default().fg(theme::foreground())
        } else {
            muted
        };
        let value = field.labeled(value);
        lines.push(Line::from(vec![
            Span::styled(if active { "▸ " } else { "  " }, style),
            Span::styled(format!("{:<label_width$}  ", origin_label(layer)), style),
            Span::styled(
                ellipsize(&value, budget),
                if active {
                    style.add_modifier(Modifier::BOLD)
                } else {
                    style
                },
            ),
        ]));
    }
    f.render_widget(Paragraph::new(lines), content);
}

pub(super) fn targets(f: &mut Frame, area: Rect, names: &[String], selected: usize) {
    let strong = Style::default()
        .fg(theme::foreground())
        .add_modifier(Modifier::BOLD);
    let dim = theme::dim(theme::muted());
    let mut labels = vec![Span::styled("Save to", strong), Span::raw("   ")];
    let mut hints = vec![Span::raw("          ")];
    for (index, name) in names.iter().enumerate() {
        let (label, hint) = (origin_label(name), hint(name));
        let width = label.width().max(hint.width());
        let active = index == selected;
        if index > 0 {
            labels.push(Span::raw("    "));
            hints.push(Span::raw("    "));
        }
        labels.push(Span::styled(
            if active { "● " } else { "○ " },
            if active {
                strong.remove_modifier(Modifier::BOLD)
            } else {
                dim
            },
        ));
        labels.push(Span::styled(
            format!("{label:<width$}"),
            if active { strong } else { dim },
        ));
        hints.push(Span::styled(
            format!("  {hint:<width$}"),
            dim.add_modifier(Modifier::ITALIC),
        ));
    }
    let labels = Line::from(labels);
    let lines = if labels.width() <= usize::from(area.width) {
        vec![labels, Line::from(hints)]
    } else {
        let name = names.get(selected).map(String::as_str).unwrap_or("");
        vec![
            Line::from(vec![
                Span::styled("Save to   ", strong),
                Span::styled(origin_label(name), strong),
            ]),
            Line::styled(hint(name), dim.add_modifier(Modifier::ITALIC)),
        ]
    };
    f.render_widget(Paragraph::new(lines), area);
}

fn hint(target: &str) -> &str {
    match target {
        "overrides" => "until restart",
        "project-toml" => "saved for this project",
        _ => "saved globally",
    }
}
