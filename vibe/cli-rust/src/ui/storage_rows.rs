//! `/storage` lines as styled runs: the header, the suggestions, session rows, details and the review.

use ratatui::style::{Modifier, Style};

use super::{list_cursor, theme};
use crate::server::{StorageSnapshot, StorageUsage, StorageWorktree};
use crate::storage_panel::actions::plural;
use crate::storage_panel::model::{self, Estimate, Row};
use crate::storage_panel::{leftovers, review, ReviewKind};
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
    let mut runs = title("Vibe storage");
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

/// A bold title in the brand color.
pub fn title(text: &str) -> Runs {
    let (base, ..) = styles(false);
    vec![run(
        text,
        base.fg(theme::primary()).add_modifier(Modifier::BOLD),
    )]
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

/// `Archive 4 active sessions inactive for 30+ days  ·  frees about 1.8 GB →`.
pub fn suggestion(snapshot: &StorageSnapshot, kind: ReviewKind, highlighted: bool) -> Runs {
    let (base, dim, bold) = styles(highlighted);
    let suggested = review::suggestion(snapshot, kind);
    let count = suggested.map_or(0, |s| s.session_ids.len());
    let days = suggested.map_or(0, |s| s.inactive_days);
    let text = match kind {
        ReviewKind::Archive => format!(
            "Archive {} inactive for {days}+ days",
            plural(count, "active session", "active sessions")
        ),
        ReviewKind::Delete => format!(
            "Delete {} inactive for {days}+ days",
            plural(count, "archived session", "archived sessions")
        ),
        ReviewKind::Cleanup => leftovers::suggestion_text(snapshot),
    };
    let estimate = review::suggested_estimate(snapshot, kind);
    vec![
        run(text, base),
        run(format!("  ·  frees {} ", estimate.about()), dim),
        run(decimal(estimate.bytes), bold),
        run(" →", base),
    ]
}

/// Why the server's old archived sessions have no delete row here: none can be deleted from this
/// client (open elsewhere or this session); `None` otherwise, the delete row then saying it.
pub fn old_archived(snapshot: &StorageSnapshot, current: Option<&str>) -> Option<Runs> {
    let suggestion = &snapshot.delete_suggestion;
    let count = suggestion.session_ids.len();
    let deletable = review::review_order(snapshot, ReviewKind::Delete, current, false);
    if count == 0 || !deletable.is_empty() {
        return None;
    }
    let days = suggestion.inactive_days;
    let text = match count {
        1 => format!("1 archived session inactive for {days}+ days is open, so it can't be deleted now."),
        n => format!("{n} archived sessions inactive for {days}+ days are open, so they can't be deleted now."),
    };
    Some(dim(&text))
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

/// `history 236 MB  ·  worktree 1.2 GB on <branch>  ·  copy-on-write`, then one line per other worktree.
pub fn details(row: &Row) -> Vec<Runs> {
    let (base, dim, _) = styles(false);
    let mut first = vec![
        run("history ", dim),
        run(decimal(row.session.history_bytes), base),
    ];
    let Some((primary, others)) = row.worktrees.split_first() else {
        first.push(run("  ·  no worktree", dim));
        return vec![first];
    };
    first.push(run("  ·  ", dim));
    first.extend(worktree(primary));
    let mut lines = vec![first];
    lines.extend(others.iter().map(|w| worktree(w)));
    lines
}

fn worktree(worktree: &StorageWorktree) -> Runs {
    let (base, dim, _) = styles(false);
    let size = worktree
        .bytes
        .map_or_else(|| "not measured yet".to_owned(), decimal);
    let mut runs = vec![run("worktree ", dim), run(size, base)];
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

/// `Archive 3 sessions →   frees about 1.8 GB  ·  uncommitted work is saved to a git ref`; the size
/// is left out when unknown.
pub fn estimate(kind: ReviewKind, count: usize, estimate: Option<Estimate>) -> Runs {
    let (_, dim, bold) = styles(false);
    let (verb, what, tail) = match kind {
        ReviewKind::Archive => (
            "Archive",
            "session",
            "uncommitted work is saved to a git ref",
        ),
        ReviewKind::Delete => (
            "Delete",
            "session",
            "uncommitted work is saved to a git ref",
        ),
        ReviewKind::Cleanup => ("Clean up", "item", "removed for good"),
    };
    let mut runs = vec![run(
        format!("{verb} {} →", plural(count, what, &format!("{what}s"))),
        bold,
    )];
    if let Some(estimate) = estimate {
        runs.push(run(format!("   frees {} ", estimate.about()), dim));
        runs.push(run(decimal(estimate.bytes), bold));
    }
    runs.push(run(format!("  ·  {tail}"), dim));
    runs
}

/// A line of plain dim text.
pub fn dim(text: &str) -> Runs {
    vec![run(text, styles(false).1)]
}
