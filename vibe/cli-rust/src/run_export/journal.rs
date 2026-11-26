//! Writing the export file and copying the session journal into the output
//! directory.

use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use super::model::RunExport;
use super::redact::redact_metadata_config;
use super::{EXPORT_FILENAME, JOURNAL_DIRNAME, METADATA_FILENAME, SUBAGENTS_DIRNAME};

/// Partial-write files the session writer can leave behind. They hold
/// unredacted config, so they never enter the export.
const PARTIAL_WRITE_SUFFIXES: [&str; 2] = [".json.tmp", ".jsonl.tmp"];

/// Write the export atomically: stage then rename.
pub fn write_run_export(output_dir: &Path, export: &RunExport) -> std::io::Result<PathBuf> {
    std::fs::create_dir_all(output_dir)?;
    let path = output_dir.join(EXPORT_FILENAME);
    let staged = output_dir.join(format!(".{EXPORT_FILENAME}.tmp"));
    let json = serde_json::to_string_pretty(export)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    std::fs::write(&staged, format!("{json}\n"))?;
    replace_file(&staged, &path)?;
    Ok(path)
}

/// Rename a staged file over its destination, replacing it, which `fs::rename`
/// does not do on Windows. The staged file is complete before the old one goes.
fn replace_file(staged: &Path, path: &Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        std::fs::rename(staged, path)
    }
    #[cfg(not(unix))]
    {
        if path.exists() {
            std::fs::remove_file(path)?;
        }
        std::fs::rename(staged, path)
    }
}

/// Copy a session directory into the output directory; returns its name there.
/// Stages the copy in a temp directory first. The previous journal is then
/// set aside, the copy renamed into its place, and only then the previous
/// journal removed, so a failed copy does not destroy the previous journal.
/// The copy's config snapshot is redacted as the export's is, since the
/// output directory is shared more widely than the session directory.
pub fn copy_journal(session_dir: &Path, output_dir: &Path) -> std::io::Result<String> {
    replace_dir(output_dir, JOURNAL_DIRNAME, |staged| {
        copy_session(session_dir, staged)
    })?;
    Ok(JOURNAL_DIRNAME.to_owned())
}

/// Copy each subagent's session directory under `subagents/<session id>`,
/// staged, redacted and swapped in as one as `copy_journal` does; returns
/// each one's directory relative to the output directory. With none to copy,
/// an earlier export's `subagents/` goes.
pub fn copy_subagent_journals(
    session_dirs: &[(String, PathBuf)],
    output_dir: &Path,
) -> std::io::Result<Vec<(String, String)>> {
    if session_dirs.is_empty() {
        let target = output_dir.join(SUBAGENTS_DIRNAME);
        recover_previous_journal(
            &output_dir.join(format!(".{SUBAGENTS_DIRNAME}.old")),
            &target,
        )?;
        if target.exists() {
            std::fs::remove_dir_all(&target)?;
        }
        return Ok(Vec::new());
    }
    replace_dir(output_dir, SUBAGENTS_DIRNAME, |staged| {
        std::fs::create_dir_all(staged)?;
        for (session_id, session_dir) in session_dirs {
            copy_session(session_dir, &staged.join(session_id))?;
        }
        Ok(())
    })?;
    Ok(session_dirs
        .iter()
        .map(|(id, _)| (id.clone(), format!("{SUBAGENTS_DIRNAME}/{id}")))
        .collect())
}

/// Stage `name` in the output directory and swap it in over the previous
/// one. A failed copy removes its staging directory, which may hold an
/// unredacted snapshot.
fn replace_dir(
    output_dir: &Path,
    name: &str,
    stage: impl FnOnce(&Path) -> std::io::Result<()>,
) -> std::io::Result<()> {
    let target = output_dir.join(name);
    let staged = output_dir.join(format!(".{name}.tmp"));
    let previous = output_dir.join(format!(".{name}.old"));
    recover_previous_journal(&previous, &target)?;
    if staged.exists() {
        std::fs::remove_dir_all(&staged)?;
    }
    let swapped = stage(&staged).and_then(|()| swap_in(&staged, &target, &previous));
    if swapped.is_err() {
        let _ = std::fs::remove_dir_all(&staged);
    }
    swapped
}

/// Settle a journal an earlier, crashed copy left aside: with a journal in
/// place the set-aside one is older and goes, without one the copy crashed
/// between setting it aside and renaming its own copy in, so the set-aside
/// one is the latest journal and goes back.
fn recover_previous_journal(previous: &Path, target: &Path) -> std::io::Result<()> {
    if !previous.exists() {
        return Ok(());
    }
    if target.exists() {
        std::fs::remove_dir_all(previous)
    } else {
        std::fs::rename(previous, target)
    }
}

/// Copy a session directory and redact its config snapshot.
fn copy_session(session_dir: &Path, target: &Path) -> std::io::Result<()> {
    copy_dir_recursive(session_dir, target)?;
    redact_metadata_config(&target.join(METADATA_FILENAME));
    Ok(())
}

/// Rename the staged copy over the target. The previous journal is set
/// aside first and restored if the swap fails, and removed only once the
/// copy is in place, so a failed swap leaves the previous journal. A
/// directory cannot replace a non-empty one, so the target is renamed aside
/// rather than deleted.
fn swap_in(staged: &Path, target: &Path, previous: &Path) -> std::io::Result<()> {
    if !target.exists() {
        return std::fs::rename(staged, target);
    }
    std::fs::rename(target, previous)?;
    match std::fs::rename(staged, target) {
        Ok(()) => {
            let _ = std::fs::remove_dir_all(previous);
            Ok(())
        }
        Err(error) => {
            let _ = std::fs::rename(previous, target);
            Err(error)
        }
    }
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        // A partial write holds unredacted config; it stays out of the copy.
        if is_partial_write(&entry.file_name()) {
            continue;
        }
        let ty = entry.file_type()?;
        let from = entry.path();
        let to = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir_recursive(&from, &to)?;
        } else if ty.is_symlink() {
            copy_symlink(&from, &to)?;
        } else {
            std::fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

/// Whether a file name is a partial write (`*.json.tmp`, `*.jsonl.tmp`).
fn is_partial_write(name: &OsStr) -> bool {
    let name = name.to_string_lossy();
    PARTIAL_WRITE_SUFFIXES
        .iter()
        .any(|suffix| name.ends_with(suffix))
}

/// Recreate a symlink in the copy as a link, not as the file it points at.
#[cfg(unix)]
fn copy_symlink(from: &Path, to: &Path) -> std::io::Result<()> {
    let link = std::fs::read_link(from)?;
    std::os::unix::fs::symlink(&link, to)
}

/// Where symlinks cannot be created, copy what the link points at instead.
#[cfg(not(unix))]
fn copy_symlink(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::copy(from, to).map(|_| ())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dir_with_file(root: &Path, name: &str, file: &str, content: &str) -> PathBuf {
        let dir = root.join(name);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(file), content).unwrap();
        dir
    }

    #[test]
    fn swap_in_renames_the_copy_in_without_a_target() {
        let root = tempfile::tempdir().unwrap();
        let staged = dir_with_file(root.path(), "staged", "journal.jsonl", "new");
        let target = root.path().join("session");
        let previous = root.path().join(".session.old");

        swap_in(&staged, &target, &previous).unwrap();

        assert_eq!(
            std::fs::read_to_string(target.join("journal.jsonl")).unwrap(),
            "new"
        );
        assert!(!staged.exists());
        assert!(!previous.exists());
    }

    #[test]
    fn swap_in_replaces_the_previous_journal_on_success() {
        let root = tempfile::tempdir().unwrap();
        let staged = dir_with_file(root.path(), "staged", "journal.jsonl", "new");
        let target = dir_with_file(root.path(), "session", "journal.jsonl", "old");
        let previous = root.path().join(".session.old");

        swap_in(&staged, &target, &previous).unwrap();

        assert_eq!(
            std::fs::read_to_string(target.join("journal.jsonl")).unwrap(),
            "new"
        );
        assert!(!staged.exists());
        assert!(!previous.exists());
    }

    #[test]
    fn swap_in_restores_the_previous_journal_when_the_copy_fails() {
        let root = tempfile::tempdir().unwrap();
        // A staged path that does not exist fails its rename after the
        // previous journal was set aside.
        let staged = root.path().join("staged");
        let target = dir_with_file(root.path(), "session", "journal.jsonl", "old");
        let previous = root.path().join(".session.old");

        let error = swap_in(&staged, &target, &previous).unwrap_err();

        assert_eq!(error.kind(), std::io::ErrorKind::NotFound);
        // The previous journal is back in place, and nothing is left aside.
        assert_eq!(
            std::fs::read_to_string(target.join("journal.jsonl")).unwrap(),
            "old"
        );
        assert!(!previous.exists());
    }

    #[test]
    fn recover_discards_a_set_aside_journal_older_than_the_target() {
        let root = tempfile::tempdir().unwrap();
        let target = dir_with_file(root.path(), "session", "journal.jsonl", "current");
        let previous = dir_with_file(root.path(), ".session.old", "journal.jsonl", "older");

        recover_previous_journal(&previous, &target).unwrap();

        assert!(!previous.exists());
        assert_eq!(
            std::fs::read_to_string(target.join("journal.jsonl")).unwrap(),
            "current"
        );
    }

    #[test]
    fn recover_restores_a_set_aside_journal_without_a_target() {
        let root = tempfile::tempdir().unwrap();
        let target = root.path().join("session");
        let previous = dir_with_file(root.path(), ".session.old", "journal.jsonl", "latest");

        recover_previous_journal(&previous, &target).unwrap();

        assert!(!previous.exists());
        assert_eq!(
            std::fs::read_to_string(target.join("journal.jsonl")).unwrap(),
            "latest"
        );
    }
}
