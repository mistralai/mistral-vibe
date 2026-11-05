//! Ignore-rule changes matter only when traversal reaches their directory.

use std::ffi::OsStr;
use std::path::Path;

use ignore::IncrementalIgnore;

use super::is_git_path;

pub(super) fn relevant(root: &Path, path: &Path, ignore: &mut IncrementalIgnore) -> bool {
    let Ok(relative) = path.strip_prefix(root) else {
        return false;
    };
    let directory = if relative.ends_with(Path::new(".git").join("info").join("exclude")) {
        relative
            .parent()
            .and_then(Path::parent)
            .and_then(Path::parent)
    } else if matches!(relative.file_name(), Some(name) if name == OsStr::new(".gitignore") || name == OsStr::new(".ignore"))
    {
        relative.parent()
    } else {
        return false;
    };
    // Match the containing directory, not the ignore file: rules can exclude their own filename.
    directory.is_some_and(|directory| {
        !is_git_path(directory) && !ignore.matched(directory, true).is_ignore()
    })
}
