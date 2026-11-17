//! Pure views of a storage snapshot: session rows, their badges, and the archive estimate.

use std::collections::HashSet;

use crate::server::{StorageSession, StorageSnapshot, StorageWorktree};

const DAY_MS: u64 = 86_400_000;

/// One session row, joined with its worktree.
#[derive(Clone, Copy)]
pub struct Row<'a> {
    pub session: &'a StorageSession,
    pub worktree: Option<&'a StorageWorktree>,
    /// The attached session of this CLI.
    pub current: bool,
}

impl<'a> Row<'a> {
    /// History plus the full worktree size, shared or not.
    pub fn bytes(&self) -> u64 {
        self.session.history_bytes + self.worktree.and_then(|w| w.bytes).unwrap_or(0)
    }

    pub fn title(&self) -> &'a str {
        match self.session.title.as_deref() {
            Some(title) if !title.trim().is_empty() => title,
            _ => "Untitled session",
        }
    }

    /// The repository name, else the last folder of the session's cwd.
    pub fn project(&self) -> &'a str {
        let cwd = self.session.cwd.as_deref().unwrap_or_default();
        self.worktree
            .and_then(|w| w.repo_name.as_deref())
            .unwrap_or_else(|| {
                cwd.rsplit(['/', '\\'])
                    .find(|p| !p.is_empty())
                    .unwrap_or("")
            })
    }

    /// Archive and delete are off: a process holds the session.
    pub fn locked(&self) -> bool {
        self.current || self.session.open
    }

    /// The trailing state words: `open · shared by 2`.
    pub fn badges(&self) -> String {
        let mut badges = Vec::new();
        if self.current {
            badges.push("this session".to_owned());
        } else if self.session.open {
            badges.push("open".to_owned());
        }
        if self.session.archived {
            badges.push("archived".to_owned());
        }
        match self.worktree {
            None => badges.push("no worktree".to_owned()),
            Some(worktree) => {
                if worktree.session_ids.len() > 1 {
                    badges.push(format!("shared by {}", worktree.session_ids.len()));
                }
                if worktree.bytes.is_none() {
                    badges.push("not measured yet".to_owned());
                }
            }
        }
        badges.join(" · ")
    }
}

/// Every session row, largest first.
pub fn rows<'a>(snapshot: &'a StorageSnapshot, current: Option<&str>) -> Vec<Row<'a>> {
    let mut rows: Vec<Row> = snapshot
        .sessions
        .iter()
        .map(|session| Row {
            session,
            worktree: session
                .worktree_id
                .as_deref()
                .and_then(|id| snapshot.worktrees.iter().find(|w| w.id == id)),
            current: current == Some(session.session_id.as_str()),
        })
        .collect();
    rows.sort_by_key(|row| std::cmp::Reverse(row.bytes()));
    rows
}

/// The suggested sessions still listed, by size or oldest activity first.
pub fn review_order(snapshot: &StorageSnapshot, by_date: bool) -> Vec<String> {
    let suggested: HashSet<&str> = snapshot
        .archive_suggestion
        .session_ids
        .iter()
        .map(String::as_str)
        .collect();
    let mut rows: Vec<Row> = rows(snapshot, None)
        .into_iter()
        .filter(|row| suggested.contains(row.session.session_id.as_str()))
        .collect();
    if by_date {
        rows.sort_by_key(|row| row.session.last_activity_at);
    }
    rows.into_iter()
        .map(|row| row.session.session_id.clone())
        .collect()
}

/// Space archiving `selected` frees; `at_least` when a worktree it frees is not measured yet.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Estimate {
    pub bytes: u64,
    pub at_least: bool,
}

/// A worktree counts once every session using it is selected or already archived; histories stay.
pub fn estimate(snapshot: &StorageSnapshot, selected: &HashSet<String>) -> Estimate {
    let archived: HashSet<&str> = snapshot
        .sessions
        .iter()
        .filter(|s| s.archived)
        .map(|s| s.session_id.as_str())
        .collect();
    let mut estimate = Estimate::default();
    for worktree in &snapshot.worktrees {
        let ids = &worktree.session_ids;
        let freed = ids.iter().any(|id| selected.contains(id))
            && ids
                .iter()
                .all(|id| selected.contains(id) || archived.contains(id.as_str()));
        if !freed {
            continue;
        }
        match worktree.bytes {
            Some(bytes) => estimate.bytes += bytes,
            None => estimate.at_least = true,
        }
    }
    estimate
}

/// Space the scan measured freed: the bytes of the worktrees archiving
/// `archived` freed in `before` that `after` no longer lists. A worktree the
/// scan still finds, or one `before` had not measured, adds nothing, so
/// unrelated changes between the two scans never count.
pub fn measured_freed(
    before: &StorageSnapshot,
    after: &StorageSnapshot,
    archived: &HashSet<String>,
) -> u64 {
    let gone = |id: &str| !after.worktrees.iter().any(|w| w.id == id);
    let previously_archived: HashSet<&str> = before
        .sessions
        .iter()
        .filter(|s| s.archived)
        .map(|s| s.session_id.as_str())
        .collect();
    before
        .worktrees
        .iter()
        .filter(|w| {
            w.session_ids.iter().any(|id| archived.contains(id))
                && w.session_ids
                    .iter()
                    .all(|id| archived.contains(id) || previously_archived.contains(id.as_str()))
        })
        .filter(|w| gone(&w.id))
        .filter_map(|w| w.bytes)
        .sum()
}

/// `today`, `3d ago`: days since the last activity.
pub fn age(last_activity_at: u64, now_ms: u64) -> String {
    match now_ms.saturating_sub(last_activity_at) / DAY_MS {
        0 => "today".to_owned(),
        days => format!("{days}d ago"),
    }
}

/// `updated 2 minutes ago`, from the snapshot's measurement time.
pub fn updated(measured_at: u64, now_ms: u64) -> String {
    let minutes = now_ms.saturating_sub(measured_at) / 60_000;
    let (count, unit) = match minutes {
        0 => return "updated just now".to_owned(),
        1..=59 => (minutes, "minute"),
        60..=1_439 => (minutes / 60, "hour"),
        _ => (minutes / 1_440, "day"),
    };
    let plural = if count == 1 { "" } else { "s" };
    format!("updated {count} {unit}{plural} ago")
}
