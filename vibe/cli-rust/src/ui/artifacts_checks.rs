//! The artifacts modal's CI checks: colored marks, failures first, the rest counted.

use ratatui::style::Style;

use super::theme;
use crate::server::PublicSessionArtifact;

/// Checks listed before the rest collapse into one `N more passed` line; four checks are all listed, since one hidden check would take that line anyway.
pub const MAX_CHECK_LINES: usize = 3;

/// A CI check's outcome, in the order the block lists them: failures first.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Outcome {
    Failed,
    Pending,
    Passed,
}

impl Outcome {
    pub fn of(status: Option<&str>) -> Self {
        match status {
            Some("fail") => Self::Failed,
            Some("pass") => Self::Passed,
            _ => Self::Pending,
        }
    }

    /// The mark leading the check's line, in the color of its outcome.
    pub fn mark(self) -> (&'static str, Style) {
        match self {
            Self::Failed => ("✗", theme::text(theme::text_error())),
            Self::Pending => ("○", theme::muted_style()),
            Self::Passed => ("✓", theme::text(theme::text_success())),
        }
    }

    fn word(self) -> &'static str {
        match self {
            Self::Failed => "failed",
            Self::Pending => "pending",
            Self::Passed => "passed",
        }
    }
}

/// One line of the checks block: a check, or the count of the hidden ones.
#[derive(Debug, PartialEq, Eq)]
pub enum CheckLine {
    Check(Outcome, String),
    More(String),
}

/// An artifact's CI checks (Phase 4): failures first, then pending, then passed.
pub fn lines(artifact: Option<&PublicSessionArtifact>) -> Vec<CheckLine> {
    let Some(checks) = artifact
        .and_then(|artifact| artifact.details.as_ref())
        .and_then(|details| details.checks.as_ref())
    else {
        return Vec::new();
    };
    let mut entries: Vec<(Outcome, String)> = checks
        .checks
        .iter()
        .filter_map(|check| Some((Outcome::of(check.status.as_deref()), check.name.clone()?)))
        .collect();
    entries.sort_by_key(|(outcome, _)| *outcome);
    // A single hidden check would take the summary's line anyway, so it is listed instead.
    let listed = if entries.len() > MAX_CHECK_LINES + 1 {
        MAX_CHECK_LINES
    } else {
        entries.len()
    };
    let hidden = entries.split_off(listed);
    let mut lines: Vec<CheckLine> = entries
        .into_iter()
        .map(|(outcome, name)| CheckLine::Check(outcome, name))
        .collect();
    if let Some((first, _)) = hidden.first() {
        let alike = hidden.iter().all(|(outcome, _)| outcome == first);
        let word = if alike {
            format!(" {}", first.word())
        } else {
            String::new()
        };
        lines.push(CheckLine::More(format!("{} more{word}", hidden.len())));
    }
    lines
}
