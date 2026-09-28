//! Trust gate row construction and overflow layout.

use ratatui::style::Modifier;

use super::theme;
use super::trust_folders_text::{block, Row};
use crate::trust_folders::TrustFolders;

/// The dialog's rows plus what the overflowing scroll region needs.
pub(super) struct Layout {
    pub(super) rows: Vec<Row>,
    /// Rows belonging to `#trust-dialog-content`, narrowed by its scrollbar.
    pub(super) scroll_rows: usize,
    pub(super) scroll_width: usize,
    /// Unclipped scroll-content height, `None` while everything fits.
    pub(super) virtual_rows: Option<usize>,
    /// First scroll row shown, already clamped to what the region can reach.
    pub(super) scroll: usize,
    /// Largest `scroll` that still fills the region.
    pub(super) scroll_max: usize,
}

/// `#trust-dialog-content { min-height: 3; max-height: 10 }`.
const CONTENT_MIN: usize = 3;
pub(super) const CONTENT_MAX: usize = 10;
/// `.trust-option { margin: 0 3 }`, collapsed between two adjacent options.
const OPTION_MARGIN: usize = 3;

const WARNING: &str = "Malicious configs can modify AI behavior, exfiltrate data, run destructive \
                       commands, or silently alter your code.";

pub(super) fn build(
    trust: &TrustFolders,
    content_width: usize,
    available_scroll_rows: usize,
) -> Layout {
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
    let scroll_rows = available_scroll_rows.min(CONTENT_MAX);
    let full = scroll_content(trust, content_width);
    let overflows = full.len() > scroll_rows;
    let scroll_width = content_width - usize::from(overflows);
    let mut scrolled = match overflows {
        true => scroll_content(trust, scroll_width),
        false => full,
    };
    let virtual_rows = overflows.then_some(scrolled.len());
    let scroll_max = scrolled.len().saturating_sub(scroll_rows);
    let scroll = trust.scroll.min(scroll_max);
    scrolled.drain(..scroll);
    scrolled.truncate(scroll_rows);
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
    rows.push(Row::default());
    rows.push(help_row());
    rows.push(Row::default());
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
pub(super) fn scroll_content(trust: &TrustFolders, content_width: usize) -> Vec<Row> {
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
        rows.push(Row::default());
    }
    rows
}

fn files_section(title: &str, files: &[String], content_width: usize) -> Vec<Row> {
    if files.is_empty() {
        return Vec::new();
    }
    let mut rows = vec![Row::text(vec![(
        title.to_owned(),
        theme::muted_style().add_modifier(Modifier::BOLD),
    )])];
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

fn footer_warning(trust: &TrustFolders, content_width: usize) -> Vec<Row> {
    let rule = Row::decoration(vec![(
        "─".repeat(content_width),
        theme::text(theme::border_blurred()),
    )]);
    let title = Row::text(vec![(
        trust.title().to_owned(),
        theme::text(theme::warning()).add_modifier(Modifier::BOLD),
    )]);
    vec![rule.clone(), title, rule, Row::default()]
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
    let mut row = vec![(margin.clone(), plain)];
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
    Row::widgets(row)
}

fn help_row() -> Row {
    let key = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    let muted = theme::muted_style();
    Row::text(vec![
        ("\u{2191}\u{2193}".to_owned(), key),
        (" scroll  ".to_owned(), muted),
        ("\u{2190}\u{2192}".to_owned(), key),
        (" navigate  ".to_owned(), muted),
        ("Enter".to_owned(), key),
        (" select".to_owned(), muted),
    ])
}
