//! Ignore-rule changes matter only when traversal reaches their directory.

use std::ffi::OsStr;
use std::path::Path;

use ignore::IncrementalIgnore;

use super::is_git_path;

pub(super) fn relevant(root: &Path, path: &Path, ignore: &mut IncrementalIgnore) -> bool {
    let Ok(relative) = path.strip_prefix(root) else {
        return false;
    };
    // Exclude rules also apply to worktrees outside the repository directory.
    if relative.ends_with(Path::new(".git").join("info").join("exclude")) {
        return true;
    }
    if !matches!(relative.file_name(), Some(name) if name == OsStr::new(".gitignore") || name == OsStr::new(".ignore"))
    {
        return false;
    }
    // Match the existing parent, not the rule file (it can ignore itself) nor a deleted one (it would be cached).
    relative.parent().is_some_and(|directory| {
        !is_git_path(directory)
            && path.parent().is_some_and(Path::is_dir)
            && !ignore.matched(directory, true).is_ignore()
    })
}
