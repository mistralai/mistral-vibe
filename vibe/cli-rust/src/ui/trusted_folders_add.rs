//! `/trusted-folders` add view: the path field, the folders it offers, and what trusting the highlighted one does.

use ratatui::layout::Rect;
use ratatui::Frame;

use super::browser_box::{self, draw_frame};
use super::theme;
use super::visual_lines::{merge, text, wrap_indented, VisualLine};
use crate::app::App;
use crate::mouse::MouseTarget;
use crate::trusted_folders::add::Add;
use crate::trusted_folders::add_text::{self, Tone};

/// Rows above the lines: the title, its blank, the field and its blank.
const HEAD: u16 = 4;

/// The add view's lines, wrapped to `width`: the status, the candidates, then the highlighted one's details.
pub fn lay_out(add: &Add, width: usize) -> Vec<VisualLine> {
    let mut out: Vec<VisualLine> = Vec::new();
    if let Some((tone, line)) = add_text::status(add) {
        out.extend(wrapped(tone, &line, width));
        out.push(plain(Vec::new()));
    }
    for (index, candidate) in add.candidates().iter().enumerate() {
        let highlighted = index == add.candidates.selected();
        let (base, dim) = super::list_cursor::styles(highlighted);
        let (path, note) = add_text::row(candidate);
        let mut chars = text(&format!("  {path}"), base);
        if let Some(note) = note {
            chars.extend(text(&format!("  {note}"), dim));
        }
        chars.truncate(width);
        out.push(VisualLine {
            option: Some(index),
            highlighted,
            spans: merge(&chars),
        });
    }
    let details = add_text::details(add);
    if !details.is_empty() {
        out.push(plain(Vec::new()));
    }
    for (tone, line) in details {
        out.extend(wrapped(tone, &line, width));
    }
    out
}

fn wrapped(tone: Tone, line: &str, width: usize) -> Vec<VisualLine> {
    let style = match tone {
        Tone::Plain => theme::text(theme::foreground()),
        Tone::Muted => theme::muted_style(),
        Tone::Warning => theme::text(theme::text_warning()),
        Tone::Error => theme::text(theme::text_error()),
    };
    wrap_indented(line, style, width)
        .into_iter()
        .map(plain)
        .collect()
}

fn plain(chars: Vec<super::visual_lines::Sc>) -> VisualLine {
    VisualLine {
        option: None,
        highlighted: false,
        spans: merge(&chars),
    }
}

/// Rows a box `width` wide needs around `lines`.
pub fn chrome(add: &Add, width: u16) -> u16 {
    browser_box::chrome(true, width, &add_text::help(add))
}

pub fn draw_box(app: &mut App, f: &mut Frame, area: Rect, lines: &[VisualLine]) {
    let Some(chrome) = app
        .trusted_folders
        .add
        .as_ref()
        .map(|add| chrome(add, area.width))
    else {
        return;
    };
    if area.height < chrome {
        return;
    }
    draw_frame(f, area, "Trust a folder");
    let row = Rect::new(area.x + 3, area.y + 2, area.width.saturating_sub(6), 1);
    let cursor_on = app.view.cursor_on;
    let Some(add) = app.trusted_folders.add.as_ref() else {
        return;
    };
    let placeholder = "Path of the folder to trust";
    super::search_field::draw_field(
        f,
        row,
        &add.field,
        placeholder,
        cursor_on,
        theme::background(),
    );
    app.view
        .bottom_app_selection_chrome
        .push((row.y, row.x, row.right().saturating_sub(1)));
    let visible = area.height.saturating_sub(chrome);
    let pane = Rect::new(
        area.x + 1,
        area.y + HEAD,
        area.width.saturating_sub(2),
        visible,
    );
    let options: Vec<_> = lines.iter().map(|line| line.option).collect();
    let target = MouseTarget::TrustedFolders;
    crate::mouse::register_region(app, pane, target);
    let offset = app
        .trusted_folders
        .add
        .as_mut()
        .map_or(0, |add| add.candidates.layout(&options, pane));
    browser_box::draw_lines(app, f, area, pane, lines, offset, target);
    if let Some(add) = app.trusted_folders.add.as_ref() {
        browser_box::draw_help(f, area, &add_text::help(add));
    }
}
