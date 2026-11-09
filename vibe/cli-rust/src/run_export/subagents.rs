//! The export's subagents, each with its journal copied beside the root's.

use std::path::{Component, Path, PathBuf};

use super::journal::copy_subagent_journals;
use super::model::{RunSubagent, SubagentReport};

/// Copy the subagents' journals into the output directory and return their
/// export entries, None when the session spawned none. A subagent without a
/// store, as a failed spawn is, has no journal; a failed copy leaves all
/// without one rather than fail the export.
pub fn export_subagents(output_dir: &Path, reports: &[SubagentReport]) -> Option<Vec<RunSubagent>> {
    let session_dirs: Vec<(String, PathBuf)> = reports
        .iter()
        .filter_map(|report| {
            let dir = report.session_dir.as_ref()?;
            let id = &report.subagent.session_id;
            (is_plain_name(id) && dir.is_dir()).then(|| (id.clone(), dir.clone()))
        })
        .collect();
    let journal_dirs = copy_subagent_journals(&session_dirs, output_dir).unwrap_or_else(|e| {
        tracing::warn!("Could not copy the subagents' journals: {e}");
        Vec::new()
    });
    if reports.is_empty() {
        return None;
    }
    let journal_dir = |id: &str| {
        journal_dirs
            .iter()
            .find(|(session_id, _)| session_id == id)
            .map(|(_, dir)| dir.clone())
    };
    Some(
        reports
            .iter()
            .map(|report| RunSubagent {
                journal_dir: journal_dir(&report.subagent.session_id),
                ..report.subagent.clone()
            })
            .collect(),
    )
}

/// A session id is a directory name in the export, so it names no other path.
fn is_plain_name(id: &str) -> bool {
    let mut parts = Path::new(id).components();
    matches!(
        (parts.next(), parts.next()),
        (Some(Component::Normal(_)), None)
    )
}
