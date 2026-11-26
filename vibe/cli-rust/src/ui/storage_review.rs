//! The `/storage` bulk reviews: their header, the checked rows and the estimate under them.

use super::list_cursor::padded;
use super::storage_panel::Line;
use super::storage_rows::{self as rows, styles, Runs};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::server::StorageSnapshot;
use crate::storage_panel::model::Row;
use crate::storage_panel::{leftovers, review, ReviewKind};
use crate::utils::bytes::decimal;

/// The header lines, the checked rows and the estimate line.
pub(super) fn lines(
    app: &App,
    snapshot: &StorageSnapshot,
    all: &[Row],
    now: u64,
    (width, title_width): (usize, usize),
) -> (Vec<Line>, Vec<Line>, Line) {
    let review = &app.storage.review;
    let (title, why) = match review.kind {
        ReviewKind::Archive => (
            "Archive inactive sessions",
            "No activity for 30+ days. Worktrees are removed once no other session uses them; history is kept.",
        ),
        ReviewKind::Delete => (
            "Delete archived sessions",
            "Deleting removes each session's history for good. Worktrees no other session uses are removed; uncommitted work is saved to a git ref.",
        ),
        ReviewKind::Cleanup => (
            "Clean up leftovers",
            "Removes these folders for good. Worktrees no session uses are removed after their uncommitted work is saved to a git ref; other folders are deleted as they are.",
        ),
    };
    let mut top = vec![Line::text(rows::title(title))];
    let why = crate::utils::text::wrap_hard(why, width + 2);
    top.extend(why.iter().map(|line| Line::text(rows::dim(line))));
    top.push(Line::text(Vec::new()));
    let list = match review.kind {
        ReviewKind::Cleanup => leftover_lines(app, snapshot, width),
        _ => session_lines(app, all, now, title_width),
    };
    let estimate = review::review_estimate(snapshot, review.kind, &review.selected);
    let count = review.selected.len();
    let estimate = Line::text(rows::estimate(review.kind, count, estimate));
    (top, list, estimate)
}

fn session_lines(app: &App, all: &[Row], now: u64, title_width: usize) -> Vec<Line> {
    let review = &app.storage.review;
    review
        .order
        .iter()
        .enumerate()
        .filter_map(|(index, id)| {
            let row = all.iter().find(|row| row.session.session_id == *id)?;
            let highlighted = index == review.list.selected();
            let selected = review.selected.contains(id);
            let width = title_width.saturating_sub(4);
            let runs = rows::review(row, selected, highlighted, now, width);
            Some(Line {
                option: Some(index),
                highlighted,
                runs,
            })
        })
        .collect()
}

/// The leftover worktrees, then the other folders, each under its own heading.
fn leftover_lines(app: &App, snapshot: &StorageSnapshot, width: usize) -> Vec<Line> {
    let review = &app.storage.review;
    let items = leftovers::items(snapshot);
    let mut lines = Vec::new();
    for (worktrees, heading) in [
        (true, "Leftover worktrees"),
        (false, "Other folders in worktrees"),
    ] {
        let mut section = review
            .order
            .iter()
            .enumerate()
            .filter_map(|(index, id)| {
                let item = items
                    .iter()
                    .find(|i| i.id == *id && i.worktree == worktrees)?;
                let highlighted = index == review.list.selected();
                let selected = review.selected.contains(id);
                Some(Line {
                    option: Some(index),
                    highlighted,
                    runs: leftover(item, selected, highlighted, width),
                })
            })
            .peekable();
        if section.peek().is_none() {
            continue;
        }
        if !lines.is_empty() {
            lines.push(Line::text(Vec::new()));
        }
        lines.push(Line::text(rows::dim(heading)));
        lines.extend(section);
    }
    lines
}

/// `[x]  1.7 GB  dashboard · rebase-oidc   /home/user/.vibe/worktrees/…`; a folder shows only its path.
fn leftover(item: &leftovers::Item, selected: bool, highlighted: bool, width: usize) -> Runs {
    let (base, dim, bold) = styles(highlighted);
    let check = if selected { "[x] " } else { "[ ] " };
    let size = item.bytes.map_or_else(|| "?".to_owned(), decimal);
    let mut runs = vec![(check.to_owned(), base), (format!("{size:>7}  "), bold)];
    let mut room = width.saturating_sub(13);
    if item.worktree {
        let name = match (&item.name, &item.branch) {
            (name, Some(branch)) if !name.is_empty() => format!("{name} · {branch}"),
            (_, Some(branch)) => branch.clone(),
            (name, None) => name.clone(),
        };
        let name = crate::utils::text::ellipsize(&name, 34);
        runs.push((format!("{}  ", padded(&name, 34)), base));
        room = room.saturating_sub(36);
    }
    runs.push((tail(&item.path, room), dim));
    runs
}

/// The end of a path, which tells folders apart: `…/dashboard-aaaa/target`.
fn tail(path: &str, width: usize) -> String {
    let chars: Vec<char> = path.chars().collect();
    match chars.len() > width && width > 0 {
        true => format!(
            "…{}",
            chars[chars.len() + 1 - width..].iter().collect::<String>()
        ),
        false => path.to_owned(),
    }
}

pub(super) fn footer(app: &App) -> Vec<Hint> {
    let review = &app.storage.review;
    let sort = match review.by_date {
        true => action::SORT_DATE,
        false => action::SORT_SIZE,
    };
    let enter = match review.kind {
        ReviewKind::Archive => action::ARCHIVE,
        ReviewKind::Delete => action::DELETE,
        ReviewKind::Cleanup => action::CLEAN_UP,
    };
    let mut hints = vec![
        hints::NAVIGATE,
        (key::SPACE, action::TOGGLE),
        ("a", action::ALL),
    ];
    if review.kind != ReviewKind::Cleanup {
        hints.push(("s", sort));
    }
    hints.extend([(key::ENTER, enter), hints::BACK]);
    hints
}
