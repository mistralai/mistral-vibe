//! User-level Vibe paths shared by the Rust frontend.

use std::path::{Path, PathBuf};

use anyhow::{bail, Context, Result};

pub fn vibe_home() -> Option<PathBuf> {
    vibe_home_from(
        std::env::var_os("VIBE_HOME"),
        std::env::var_os("HOME"),
        std::env::var_os("USERPROFILE"),
    )
}

/// Python `is_dangerous_directory`: why running in `path` is not recommended,
/// or `None` when it is safe. Same check over an injected home for testability.
pub fn dangerous_directory_reason_at(path: &Path, home: &Path) -> Option<String> {
    let dangerous = [
        (home.to_path_buf(), "home directory"),
        (home.join("Documents"), "Documents folder"),
        (home.join("Desktop"), "Desktop folder"),
        (home.join("Downloads"), "Downloads folder"),
        (home.join("Pictures"), "Pictures folder"),
        (home.join("Movies"), "Movies folder"),
        (home.join("Music"), "Music folder"),
        (home.join("Library"), "Library folder"),
        (PathBuf::from("/Applications"), "Applications folder"),
        (PathBuf::from("/System"), "System folder"),
        (PathBuf::from("/Library"), "System Library folder"),
        (PathBuf::from("/usr"), "System usr folder"),
        (PathBuf::from("/private"), "System private folder"),
    ];
    dangerous
        .iter()
        .find(|(danger, _)| danger.as_path() == path)
        .map(|(_, description)| format!("You are in the {description}"))
}

/// Python `is_dangerous_directory` over the resolved cwd and `$HOME`.
pub fn dangerous_directory_reason() -> Option<String> {
    let cwd = std::env::current_dir().ok()?;
    let home = std::env::var_os("HOME").map(PathBuf::from)?;
    dangerous_directory_reason_at(&cwd, &home)
}

/// Replace a leading `$HOME` with `~` (Python `PathDisplay`, `_format_display_path`).
pub fn collapse_home(path: &str) -> String {
    match std::env::var("HOME") {
        Ok(home) if !home.is_empty() && path == home => "~".to_string(),
        Ok(home) if !home.is_empty() => match path.strip_prefix(&format!("{home}/")) {
            Some(rest) => format!("~/{rest}"),
            None => path.to_string(),
        },
        _ => path.to_string(),
    }
}

pub fn user_home() -> Option<PathBuf> {
    user_home_from(std::env::var_os("HOME"), std::env::var_os("USERPROFILE"))
}

pub fn user_home_from(
    home: Option<std::ffi::OsString>,
    user_profile: Option<std::ffi::OsString>,
) -> Option<PathBuf> {
    home.or(user_profile)
        .filter(|path| !path.is_empty())
        .map(PathBuf::from)
}

fn vibe_home_from(
    vibe_home: Option<std::ffi::OsString>,
    home: Option<std::ffi::OsString>,
    user_profile: Option<std::ffi::OsString>,
) -> Option<PathBuf> {
    if let Some(path) = vibe_home.filter(|path| !path.is_empty()) {
        return Some(PathBuf::from(path));
    }
    user_home_from(home, user_profile).map(|path| path.join(".vibe"))
}

/// Expand `~` in a path, since canonicalize does not.
pub fn expand_tilde(p: &Path) -> PathBuf {
    if let Ok(rest) = p.strip_prefix("~") {
        if let Some(home) = user_home() {
            return home.join(rest);
        }
    }
    p.into()
}

/// Validate and resolve `--add-dir` paths, mirroring Python entrypoint.py.
/// Relative paths resolve against the process cwd, which `--workdir` has
/// already changed by the time this runs.
pub fn resolve_add_dirs(paths: &[PathBuf]) -> Result<Vec<String>> {
    let mut resolved = Vec::new();
    for p in paths {
        let expanded = expand_tilde(p);
        let canon = expanded.canonicalize().with_context(|| {
            format!(
                "--add-dir path does not exist or is not a directory: {}",
                p.display()
            )
        })?;
        if !canon.is_dir() {
            bail!("--add-dir path is not a directory: {}", p.display());
        }
        resolved.push(
            canon
                .to_str()
                .context("--add-dir path is not valid UTF-8")?
                .to_owned(),
        );
    }
    Ok(resolved)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dangerous_directories_are_named_by_location() {
        let home = Path::new("/Users/tester");
        let reason = |path: &str| dangerous_directory_reason_at(Path::new(path), home);
        assert_eq!(
            reason("/Users/tester").as_deref(),
            Some("You are in the home directory")
        );
        assert_eq!(
            reason("/Users/tester/Downloads").as_deref(),
            Some("You are in the Downloads folder")
        );
        assert_eq!(
            reason("/private").as_deref(),
            Some("You are in the System private folder")
        );
        assert_eq!(
            reason("/usr").as_deref(),
            Some("You are in the System usr folder")
        );
    }

    #[test]
    fn safe_directories_have_no_reason() {
        let home = Path::new("/Users/tester");
        assert_eq!(
            dangerous_directory_reason_at(Path::new("/Users/tester/projects"), home),
            None
        );
        assert_eq!(dangerous_directory_reason_at(Path::new("/tmp"), home), None);
        assert_eq!(
            dangerous_directory_reason_at(Path::new("/Users/other"), home),
            None
        );
    }
}
