//! Pure views of a storage snapshot: session rows, their badges, and the archive estimate.

use std::collections::{HashMap, HashSet};

use crate::server::{StorageSession, StorageSnapshot, StorageWorktree};

const DAY_MS: u64 = 86_400_000;

/// One session row, joined with every worktree its session claims, the primary first.
#[derive(Clone)]
pub struct Row<'a> {
    pub session: &'a StorageSession,
    pub worktrees: Vec<&'a StorageWorktree>,
    /// The attached session of this CLI.
    pub current: bool,
}

impl<'a> Row<'a> {
    /// History plus the full size of every claimed worktree, shared or not.
    pub fn bytes(&self) -> u64 {
        let worktrees: u64 = self.worktrees.iter().filter_map(|w| w.bytes).sum();
        self.session.history_bytes + worktrees
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
        self.worktrees
            .first()
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

    /// The trailing state words: `open · 2 worktrees · shared by 2`.
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
        match self.worktrees.len() {
            0 => badges.push("no worktree".to_owned()),
            1 => {}
            n => badges.push(format!("{n} worktrees")),
        }
        let shared = self.worktrees.iter().map(|w| w.session_ids.len()).max();
        if let Some(shared) = shared.filter(|n| *n > 1) {
            badges.push(format!("shared by {shared}"));
        }
        if self.worktrees.iter().any(|w| w.bytes.is_none()) {
            badges.push("not measured yet".to_owned());
        }
        badges.join(" · ")
    }
}

/// Every session row, largest first.
pub fn rows<'a>(snapshot: &'a StorageSnapshot, current: Option<&str>) -> Vec<Row<'a>> {
    let by_id: HashMap<&str, &StorageWorktree> = snapshot
        .worktrees
        .iter()
        .map(|w| (w.id.as_str(), w))
        .collect();
    let mut rows: Vec<Row> = snapshot
        .sessions
        .iter()
        .map(|session| {
            let ids = match session.worktree_ids.is_empty() {
                true => session.worktree_id.as_slice(),
                false => session.worktree_ids.as_slice(),
            };
            Row {
                session,
                worktrees: ids
                    .iter()
                    .filter_map(|id| by_id.get(id.as_str()).copied())
                    .collect(),
                current: current == Some(session.session_id.as_str()),
            }
        })
        .collect();
    rows.sort_by_key(|row| std::cmp::Reverse(row.bytes()));
    rows
}

/// Space an action frees; `at_least` when part of it is not measured yet.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Estimate {
    pub bytes: u64,
    pub at_least: bool,
}

impl Estimate {
    /// `about 1.8 GB` or `at least 1.8 GB`.
    pub fn amount(&self) -> String {
        format!(
            "{} {}",
            self.about(),
            crate::utils::bytes::decimal(self.bytes)
        )
    }

    /// `about` or `at least`, for a line that styles the size apart.
    pub fn about(&self) -> &'static str {
        if self.at_least {
            "at least"
        } else {
            "about"
        }
    }
}

/// `Frees about 1.8 GB.` before an action, `Freed about 1.8 GB.` after it; nothing when unknown.
pub fn space(verb: Space, estimate: Option<Estimate>) -> String {
    let Some(estimate) = estimate else {
        return String::new();
    };
    let verb = match verb {
        Space::Frees => "Frees",
        Space::Freed => "Freed",
    };
    format!("{verb} {}.", estimate.amount())
}

/// Whether a size is an estimate shown before acting or a result written after.
#[derive(Clone, Copy)]
pub enum Space {
    Frees,
    Freed,
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
        add(&mut estimate, worktree);
    }
    estimate
}

fn add(estimate: &mut Estimate, worktree: &StorageWorktree) {
    match worktree.bytes {
        Some(bytes) => estimate.bytes += bytes,
        None => estimate.at_least = true,
    }
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
