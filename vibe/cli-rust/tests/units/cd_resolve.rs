//! `/cd` argument resolution: relative, `..`, `~`, bare and `-`.

use std::path::{Path, PathBuf};

use vibe_rs::cwd::{resolve, ResolveError};

fn at(input: &str, previous: Option<&str>) -> Result<PathBuf, ResolveError> {
    resolve(
        input,
        Path::new("/work/repo"),
        Path::new("/home/me"),
        previous.map(Path::new),
    )
}

#[test]
fn relative_paths_follow_the_session_cwd() {
    assert_eq!(at("src", None), Ok("/work/repo/src".into()));
    assert_eq!(at("./src/../lib", None), Ok("/work/repo/lib".into()));
    assert_eq!(at("..", None), Ok("/work".into()));
    assert_eq!(at("/etc/./x/..", None), Ok("/etc".into()));
}

#[test]
fn tilde_and_bare_are_home() {
    assert_eq!(at("~", None), Ok("/home/me".into()));
    assert_eq!(at("~/code", None), Ok("/home/me/code".into()));
    assert_eq!(at("", None), Ok("/home/me".into()));
    assert_eq!(at("  ", None), Ok("/home/me".into()));
}

#[test]
fn dash_is_the_previous_folder() {
    assert_eq!(at("-", Some("/old")), Ok("/old".into()));
    assert_eq!(at("-", None), Err(ResolveError::NoPrevious));
}

#[test]
fn the_move_row_names_both_folders_when_the_old_one_is_known() {
    use vibe_rs::commands::cd::moved_text;
    assert_eq!(
        moved_text(Some("/work/a"), "/work/b"),
        "Moved from `/work/a` to `/work/b`"
    );
    assert_eq!(moved_text(None, "/work/b"), "Moved to `/work/b`");
    assert_eq!(moved_text(Some(""), "/work/b"), "Moved to `/work/b`");
    assert_eq!(moved_text(Some("/work/b"), "/work/b"), "Moved to `/work/b`");
}

#[test]
fn parent_of_the_root_stays_at_the_root() {
    let home = Path::new("/home/user");
    assert_eq!(
        resolve("..", Path::new("/"), home, None),
        Ok(PathBuf::from("/"))
    );
    assert_eq!(
        resolve("/../usr", Path::new("/work"), home, None),
        Ok(PathBuf::from("/usr"))
    );
}

#[test]
fn a_quoted_or_escaped_folder_resolves_as_a_shell_reads_it() {
    let at = |input| resolve(input, Path::new("/work"), Path::new("/home/me"), None);
    assert_eq!(at("'My Folder'"), Ok(PathBuf::from("/work/My Folder")));
    assert_eq!(at("\"My Folder\""), Ok(PathBuf::from("/work/My Folder")));
    if !cfg!(windows) {
        assert_eq!(at("My\\ Folder"), Ok(PathBuf::from("/work/My Folder")));
    }
}

#[test]
fn a_windows_verbatim_path_reads_as_the_user_writes_it() {
    use vibe_rs::cwd::without_verbatim;
    assert_eq!(
        without_verbatim(PathBuf::from(r"\\?\C:\proj")),
        PathBuf::from(r"C:\proj")
    );
    assert_eq!(
        without_verbatim(PathBuf::from(r"\\?\UNC\host\share")),
        PathBuf::from(r"\\host\share")
    );
    assert_eq!(
        without_verbatim(PathBuf::from("/work/proj")),
        PathBuf::from("/work/proj")
    );
}
