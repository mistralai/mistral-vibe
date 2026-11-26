//! The client follows the folder its session runs in: path resolution and `follow`.

use std::path::{Component, Path, PathBuf};

use crate::app::App;
use crate::transcript::local;
use crate::utils::file_index::{env_opt_out, FileIndexHandle};
use crate::utils::paths::{dangerous_directory_reason_at, user_home};

#[derive(Debug, PartialEq, Eq)]
pub enum ResolveError {
    /// `-` with no folder this client moved from.
    NoPrevious,
}

/// Resolve a shell-like `cd` argument lexically: relative to `cwd`, `~` and `~/x` under
/// `home`, empty is `home`, `-` is `previous`. `.` and `..` fold without touching the disk.
pub fn resolve(
    input: &str,
    cwd: &Path,
    home: &Path,
    previous: Option<&Path>,
) -> Result<PathBuf, ResolveError> {
    let input = crate::paste_path::shell_path(input);
    let path = match input.as_str() {
        "" | "~" => home.to_path_buf(),
        "-" => previous.ok_or(ResolveError::NoPrevious)?.to_path_buf(),
        input => match home_relative(input) {
            Some(rest) => home.join(rest),
            None => cwd.join(input),
        },
    };
    Ok(normalize(&path))
}

fn normalize(path: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for component in path.components() {
        match component {
            Component::CurDir => {}
            // `..` stops at the root, as a shell's `cd /..` does.
            Component::ParentDir => {
                if out.parent().is_some() {
                    out.pop();
                }
            }
            other => out.push(other),
        }
    }
    out
}

/// What follows `~/`, or `~\` too on Windows.
fn home_relative(input: &str) -> Option<&str> {
    let rest = input.strip_prefix('~')?;
    let separator = rest
        .chars()
        .next()
        .filter(|c| std::path::is_separator(*c))?;
    Some(&rest[separator.len_utf8()..])
}

/// The canonical form of `path` when it is an existing directory, in the form
/// the user and the trust store write it: Windows' `\\?\C:\proj` is `C:\proj`,
/// or a trusted `C:\proj` would never match it.
pub fn existing_dir(path: &Path) -> Option<PathBuf> {
    let canonical = path.canonicalize().ok().filter(|path| path.is_dir())?;
    Some(without_verbatim(canonical))
}

/// `\\?\C:\x` as `C:\x` and `\\?\UNC\host\share` as `\\host\share`; any other path as is.
pub fn without_verbatim(path: PathBuf) -> PathBuf {
    let Some(text) = path.to_str() else {
        return path;
    };
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    match text.strip_prefix(r"\\?\") {
        Some(rest) if rest.as_bytes().get(1) == Some(&b':') => PathBuf::from(rest),
        _ => path,
    }
}

/// Why `cwd` is too risky to run in (or to index), shown like the startup warning.
pub fn dangerous_reason(cwd: &str) -> Option<String> {
    dangerous_directory_reason_at(Path::new(cwd), &user_home()?)
}

/// The client follows a session that now runs in `cwd`: the footer, the config resent
/// on resume, `/cd -`, the tracked worktree and the `@` index all move with it. The
/// process cwd never changes: the app-server child and other code rely on it.
pub fn follow(app: &mut App, cwd: String) {
    let shown = app.session.cwd.replace(cwd.clone());
    let previous = app.session.agent_config.cwd.replace(cwd.clone()).or(shown);
    if previous.as_deref() == Some(cwd.as_str()) {
        return;
    }
    app.session.previous_cwd = previous;
    // `--trust` was given for the launch folder: resent on a resume or a new
    // session, it would trust this one too. The `--add-dir` roots stay, as the
    // server keeps them for the live session.
    app.session.agent_config.trust_workspace = false;
    let outside =
        |worktree: &crate::worktree::WorktreeInfo| !Path::new(&cwd).starts_with(&worktree.path);
    if app.session.worktree.as_ref().is_some_and(outside) {
        app.session.worktree = None;
        app.session.prepared_worktree = None;
    }
    let reason = dangerous_reason(&cwd);
    reroot_index(app, &cwd, reason.is_some() || cwd == "/");
    if let Some(reason) =
        reason.filter(|_| app.session.startup_config.show_dangerous_directory_warning)
    {
        let text = crate::event_handler::dangerous_directory_text(&reason);
        let id = crate::commands::submission::new_message_id();
        local::add_warning(&mut app.view.transcript, &id, &text);
    }
}

/// Restart a running (or held) index at `cwd`; otherwise only park it there. A folder
/// too broad to index stays parked for good.
fn reroot_index(app: &mut App, cwd: &str, broad: bool) {
    let config = &app.session.startup_config;
    let files = &mut app.completion.files;
    let start = !broad
        && (files.running() || files.held())
        && config.enable_file_autocomplete
        && !env_opt_out();
    files.stop();
    *files = match start {
        true => FileIndexHandle::start(cwd.into(), config.file_watcher_for_autocomplete),
        false => FileIndexHandle::parked(cwd.into()),
    };
    if broad {
        files.hold();
    }
}
