//! Trust gate layout and painting: build the rows, paint them and the chrome.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::theme;
use super::trust_folders::PADDING_X;
use crate::app::App;
use crate::trust_folders::TrustFolders;
use crate::utils::text::wrap_hard;

/// One rendered line, centered as a whole in the padded content width.
pub(super) type Row = Vec<(String, Style)>;

/// The dialog's rows plus what the overflowing scroll region needs.
pub(super) struct Layout {
    pub(super) rows: Vec<Row>,
    /// Rows belonging to `#trust-dialog-content`, narrowed by its scrollbar.
    scroll_rows: usize,
    scroll_width: usize,
    /// Unclipped scroll-content height, `None` while everything fits.
    pub(super) virtual_rows: Option<usize>,
    /// First scroll row shown, already clamped to what the region can reach.
    pub(super) scroll: usize,
    /// Largest `scroll` that still fills the region.
    pub(super) scroll_max: usize,
}

/// `#trust-dialog-content { min-height: 3; max-height: 10 }`.
const CONTENT_MIN: usize = 3;
pub const CONTENT_MAX: usize = 10;
/// `.trust-option { margin: 0 3 }`, collapsed between two adjacent options.
const OPTION_MARGIN: usize = 3;

const WARNING: &str = "Malicious configs can modify AI behavior, exfiltrate data, run destructive \
                       commands, or silently alter your code.";

/// Paint the rows and report the cells that belong to no widget, which are the
/// centering padding around each row and the margins between two options.
pub(super) fn paint(
    app: &mut App,
    f: &mut Frame,
    dialog: Rect,
    content_width: usize,
    layout: &Layout,
) -> Vec<(u16, u16, u16)> {
    let left = dialog.x + 1 + PADDING_X;
    let right = left + content_width as u16 - 1;
    let top = dialog.y + 2;
    let visible = (dialog.height as usize).saturating_sub(4);
    let painted = layout.rows.len().min(visible);
    let mut chrome: Vec<(u16, u16, u16)> = (dialog.y + 1..dialog.bottom() - 1)
        .filter(|y| *y < top || *y >= top + painted as u16)
        .map(|y| (y, left, right))
        .collect();
    for (index, row) in layout.rows.iter().take(visible).enumerate() {
        let width = match index < layout.scroll_rows {
            true => layout.scroll_width,
            false => content_width,
        };
        let row_width: usize = row.iter().map(|(text, _)| text.width()).sum();
        let mut x = left + (width.saturating_sub(row_width) / 2) as u16;
        let y = top + index as u16;
        let mut segments: Vec<(u16, u16, bool)> = Vec::new();
        for (text, style) in row {
            f.buffer_mut().set_string(x, y, text, *style);
            let painted_width = text.width() as u16;
            if painted_width > 0 {
                segments.push((x, x + painted_width - 1, !text.trim().is_empty()));
            }
            x += painted_width;
        }
        let lo = segments.iter().find(|(.., filled)| *filled).map(|s| s.0);
        let hi = segments
            .iter()
            .rev()
            .find(|(.., filled)| *filled)
            .map(|s| s.1);
        let (Some(lo), Some(hi)) = (lo, hi) else {
            chrome.push((y, left, right));
            continue;
        };
        if lo > left {
            chrome.push((y, left, lo - 1));
        }
        if hi < right {
            chrome.push((y, hi + 1, right));
        }
        chrome.extend(
            segments
                .iter()
                .filter(|&&(x0, x1, filled)| !filled && x0 > lo && x1 < hi)
                .map(|&(x0, x1, _)| (y, x0, x1)),
        );
    }
    let Some(virtual_rows) = layout.virtual_rows else {
        return chrome;
    };
    let bar = Rect::new(
        left + layout.scroll_width as u16,
        top,
        1,
        layout.scroll_rows as u16,
    );
    super::scrollbar::draw(
        app,
        f,
        crate::mouse::MouseTarget::Trust,
        bar,
        virtual_rows as u16,
        layout.scroll_rows as u16,
        layout.scroll as u16,
    );
    chrome
}

pub(super) fn build(trust: &TrustFolders, content_width: usize) -> Layout {
    let Some(details) = trust.details.as_ref() else {
        return Layout {
            rows: Vec::new(),
            scroll_rows: 0,
            scroll_width: content_width,
            virtual_rows: None,
            scroll: 0,
            scroll_max: 0,
        };
    };
    // Overflowing content is re-wrapped one column narrower, for its scrollbar.
    let full = scroll_content(trust, content_width);
    let overflows = full.len() > CONTENT_MAX;
    let scroll_width = content_width - usize::from(overflows);
    let mut scrolled = match overflows {
        true => scroll_content(trust, scroll_width),
        false => full,
    };
    let virtual_rows = overflows.then_some(scrolled.len());
    let scroll_max = scrolled.len().saturating_sub(CONTENT_MAX);
    let scroll = trust.scroll.min(scroll_max);
    scrolled.drain(..scroll);
    scrolled.truncate(CONTENT_MAX);
    let scroll_rows = scrolled.len();
    let mut rows = scrolled;
    rows.extend(footer_warning(trust, content_width));
    let path_margin = trust.repo_root().is_none();
    rows.extend(block(
        &details.cwd,
        content_width,
        theme::text(theme::primary()),
        path_margin,
    ));
    rows.extend(repo_line(trust, content_width));
    rows.push(options_row(trust));
    rows.push(Vec::new());
    rows.push(help_row());
    rows.push(Vec::new());
    let save_info = format!("Setting will be saved in: {}", details.settings_path);
    rows.extend(block(
        &save_info,
        content_width,
        theme::muted_style(),
        false,
    ));
    Layout {
        rows,
        scroll_rows,
        scroll_width,
        virtual_rows,
        scroll,
        scroll_max,
    }
}

/// `#trust-dialog-content`: the warning plus the detected-file sections.
fn scroll_content(trust: &TrustFolders, content_width: usize) -> Vec<Row> {
    let Some(details) = trust.details.as_ref() else {
        return Vec::new();
    };
    let mut rows = block(
        WARNING,
        content_width,
        theme::text(theme::foreground()),
        true,
    );
    rows.extend(files_section(
        "Detected in current folder:",
        &details.detected_files,
        content_width,
    ));
    rows.extend(files_section(
        "Detected in repository context:",
        &details.repo_detected_files,
        content_width,
    ));
    while rows.len() < CONTENT_MIN {
        rows.push(Vec::new());
    }
    rows
}

fn files_section(title: &str, files: &[String], content_width: usize) -> Vec<Row> {
    if files.is_empty() {
        return Vec::new();
    }
    let mut rows = vec![vec![(
        title.to_owned(),
        theme::muted_style().add_modifier(Modifier::BOLD),
    )]];
    let listed = files
        .iter()
        .map(|file| format!("\u{2022} {file}"))
        .collect::<Vec<_>>()
        .join("\n");
    rows.extend(block(
        &listed,
        content_width,
        theme::text(theme::foreground()),
        true,
    ));
    rows
}

/// `.trust-dialog-footer-warning`: the title between two full-width rules.
fn footer_warning(trust: &TrustFolders, content_width: usize) -> Vec<Row> {
    let rule = vec![(
        "─".repeat(content_width),
        theme::text(theme::border_blurred()),
    )];
    let title = vec![(
        trust.title().to_owned(),
        theme::text(theme::warning()).add_modifier(Modifier::BOLD),
    )];
    vec![rule.clone(), title, rule, Vec::new()]
}

fn repo_line(trust: &TrustFolders, content_width: usize) -> Vec<Row> {
    let Some(repo_root) = trust.repo_root() else {
        return Vec::new();
    };
    if trust.repo_explicitly_untrusted() {
        let text = format!("\u{26a0} git repository {repo_root} is marked untrusted");
        let style = theme::text(theme::warning()).add_modifier(Modifier::ITALIC);
        return block(&text, content_width, style, true);
    }
    let text = format!("\u{21b3} git repository: {repo_root}");
    let style = theme::muted_style().add_modifier(Modifier::ITALIC);
    block(&text, content_width, style, true)
}

fn options_row(trust: &TrustFolders) -> Row {
    let margin = " ".repeat(OPTION_MARGIN);
    let plain = theme::text(theme::foreground());
    let mut row: Row = vec![(margin.clone(), plain)];
    for (index, (_, label)) in trust.options.iter().enumerate() {
        let selected = index == trust.selected;
        let cursor = if selected { "\u{203a} " } else { "  " };
        let style = match selected {
            true => plain.add_modifier(Modifier::BOLD),
            false => plain,
        };
        row.push((format!("{cursor}{label}"), style));
        row.push((margin.clone(), plain));
    }
    row
}

fn help_row() -> Row {
    let key = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    let muted = theme::muted_style();
    vec![
        ("\u{2190}\u{2192}".to_owned(), key),
        (" navigate  ".to_owned(), muted),
        ("Enter".to_owned(), key),
        (" select".to_owned(), muted),
    ]
}

/// Wrap one styled paragraph, optionally followed by its `margin-bottom: 1`.
fn block(text: &str, content_width: usize, style: Style, margin: bool) -> Vec<Row> {
    let mut rows: Vec<Row> = text
        .lines()
        .flat_map(|line| wrap_hard(line, content_width))
        .map(|line| vec![(line, style)])
        .collect();
    if margin {
        rows.push(Vec::new());
    }
    rows
}
