//! `/storage` lines as styled runs: the header, the suggestion, session rows, details and the review.

use ratatui::style::{Modifier, Style};

use super::{list_cursor, theme};
use crate::server::{StorageSnapshot, StorageUsage};
use crate::storage_panel::actions::plural;
use crate::storage_panel::model::{self, Estimate, Row};
use crate::utils::bytes::decimal;

pub type Runs = Vec<(String, Style)>;

/// Plain, dim and bold styles for a row, the bar's when highlighted.
pub fn styles(highlighted: bool) -> (Style, Style, Style) {
    let (base, dim) = list_cursor::styles(highlighted);
    (base, dim, base.add_modifier(Modifier::BOLD))
}

fn run(text: impl Into<String>, style: Style) -> (String, Style) {
    (text.into(), style)
}

/// `Vibe storage  17.6 GB used  ·  99 GB free of 494 GB  ·  updated 2 minutes ago`.
pub fn header(usage: &StorageUsage, refreshing: bool, now: u64) -> Runs {
    let (base, dim, bold) = styles(false);
    let title = base.fg(theme::primary()).add_modifier(Modifier::BOLD);
    let disk = &usage.disk;
    let free = format!(
        "{} free of {}",
        decimal(disk.free_bytes),
        decimal(disk.total_bytes)
    );
    let free_style = match disk.is_low {
        true => base.fg(theme::text_warning()).add_modifier(Modifier::BOLD),
        false => dim,
    };
    let mut runs = vec![run("Vibe storage", title)];
    let Some(snapshot) = usage.snapshot.as_ref() else {
        runs.extend([run("  ·  ", dim), run(free, free_style)]);
        return runs;
    };
    runs.extend([
        run(
            format!("  {} used", decimal(snapshot.totals.total_bytes)),
            bold,
        ),
        run("  ·  ", dim),
        run(free, free_style),
        run(
            format!("  ·  {}", status(usage, snapshot, refreshing, now)),
            dim,
        ),
    ]);
    runs
}

fn status(usage: &StorageUsage, snapshot: &StorageSnapshot, refreshing: bool, now: u64) -> String {
    let mut text = model::updated(snapshot.measured_at, now);
    if usage.status == "failed" {
        text = "last refresh failed".to_owned();
    }
    if refreshing {
        text.push_str(" · refreshing…");
    }
    if snapshot.skipped_entry_count > 0 {
        text.push_str(&format!(
            " · {} skipped",
            plural(snapshot.skipped_entry_count as usize, "entry", "entries")
        ));
    }
    if snapshot.not_measured_count > 0 {
        text.push_str(&format!(
            " · + {} not measured",
            snapshot.not_measured_count
        ));
    }
    text
}

/// The four categories.
pub fn categories(snapshot: &StorageSnapshot) -> Runs {
    let (base, dim, _) = styles(false);
    let totals = &snapshot.totals;
    [
        ("Active sessions ", totals.active_sessions_bytes),
        ("   Archived ", totals.archived_sessions_bytes),
        ("   Leftover worktrees ", totals.leftover_worktrees_bytes),
        ("   Other Vibe data ", totals.other_vibe_data_bytes),
    ]
    .into_iter()
    .flat_map(|(label, bytes)| [run(label, dim), run(decimal(bytes), base)])
    .collect()
}

pub fn low_disk() -> Runs {
    let (base, ..) = styles(false);
    vec![run(
        "⚠ Your disk is almost full",
        base.fg(theme::text_warning()).add_modifier(Modifier::BOLD),
    )]
}

/// `Archive 4 sessions inactive for 30+ days  ·  frees about 1.8 GB →`.
pub fn suggestion(snapshot: &StorageSnapshot, highlighted: bool) -> Runs {
    let (base, dim, bold) = styles(highlighted);
    let suggestion = &snapshot.archive_suggestion;
    let suggested = suggestion.session_ids.iter().cloned().collect();
    let at_least = model::estimate(snapshot, &suggested).at_least;
    vec![
        run(
            format!(
                "Archive {} inactive for 30+ days",
                plural(suggestion.session_ids.len(), "session", "sessions")
            ),
            base,
        ),
        run(
            if at_least {
                "  ·  frees at least "
            } else {
                "  ·  frees about "
            },
            dim,
        ),
        run(decimal(suggestion.reclaimable_bytes), bold),
        run(" →", base),
    ]
}

/// `  2.1 GB  42d ago   Rebase PR 71711…   dashboard   open · shared by 2`.
pub fn session(row: &Row, highlighted: bool, now: u64, title_width: usize) -> Runs {
    let (base, dim, bold) = styles(highlighted);
    let title = crate::utils::text::ellipsize(row.title(), title_width);
    let project = crate::utils::text::ellipsize(row.project(), 17);
    let mut runs = vec![
        run(format!("{:>7}", decimal(row.bytes())), bold),
        run(
            format!("  {:<8}", model::age(row.session.last_activity_at, now)),
            dim,
        ),
        run(
            format!("  {}", list_cursor::padded(&title, title_width)),
            base,
        ),
        run(format!("  {}", list_cursor::padded(&project, 17)), dim),
    ];
    let badges = row.badges();
    if !badges.is_empty() {
        runs.push(run(format!("  {badges}"), dim));
    }
    runs
}

/// `history 236 MB  ·  worktree 1.2 GB on <branch>  ·  copy-on-write`.
pub fn details(row: &Row) -> Runs {
    let (base, dim, _) = styles(false);
    let mut runs = vec![
        run("history ", dim),
        run(decimal(row.session.history_bytes), base),
    ];
    let Some(worktree) = row.worktree else {
        runs.push(run("  ·  no worktree", dim));
        return runs;
    };
    let size = worktree
        .bytes
        .map_or_else(|| "not measured yet".to_owned(), decimal);
    runs.extend([run("  ·  worktree ", dim), run(size, base)]);
    if let Some(branch) = worktree.branch.as_deref() {
        runs.extend([run(" on ", dim), run(branch, base.fg(theme::text_accent()))]);
    }
    if worktree.session_ids.len() > 1 {
        runs.push(run(
            format!("  ·  shared by {}", worktree.session_ids.len()),
            dim,
        ));
    }
    if worktree.is_copy_on_write {
        runs.push(run("  ·  copy-on-write", dim));
    }
    runs
}

/// `[x]` plus the session row, as the multi-select question draws its options.
pub fn review(row: &Row, selected: bool, highlighted: bool, now: u64, title_width: usize) -> Runs {
    let (base, ..) = styles(highlighted);
    let check = if selected { "[x] " } else { "[ ] " };
    let mut runs = vec![run(check, base)];
    runs.extend(session(row, highlighted, now, title_width));
    runs
}

/// `Archive 3 sessions →   frees about 1.8 GB  ·  uncommitted work is saved to a git ref`.
pub fn estimate(count: usize, estimate: Estimate) -> Runs {
    let (_, dim, bold) = styles(false);
    let frees = if estimate.at_least {
        "   frees at least "
    } else {
        "   frees about "
    };
    vec![
        run(
            format!("Archive {} →", plural(count, "session", "sessions")),
            bold,
        ),
        run(frees, dim),
        run(decimal(estimate.bytes), bold),
        run("  ·  uncommitted work is saved to a git ref", dim),
    ]
}

/// A line of plain dim text.
pub fn dim(text: &str) -> Runs {
    vec![run(text, styles(false).1)]
}
