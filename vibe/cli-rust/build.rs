//! Embeds the release notes, `../whats_new/<version>.md`, newest first.

use std::path::{Path, PathBuf};

fn main() {
    let manifest = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let dir = Path::new(&manifest).join("../whats_new");
    println!("cargo:rerun-if-changed={}", dir.display());
    let mut entries: Vec<((u64, u64, u64), String, PathBuf)> = std::fs::read_dir(&dir)
        .unwrap_or_else(|err| panic!("cannot read {}: {err}", dir.display()))
        .map(|file| file.expect("whats_new directory entry").path())
        .filter(|path| !file_name(path).starts_with('.'))
        .map(|path| {
            let version = file_name(&path)
                .strip_suffix(".md")
                .unwrap_or_else(|| panic!("{} is not a <version>.md file", path.display()))
                .to_owned();
            (parse(&version, &path), version, path)
        })
        .collect();
    entries.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    let rows: String = entries
        .iter()
        .map(|(_, version, path)| format!("    ({version:?}, include_str!({path:?})),\n"))
        .collect();
    let out = Path::new(&std::env::var("OUT_DIR").expect("OUT_DIR")).join("whats_new.rs");
    std::fs::write(out, format!("&[\n{rows}]\n")).expect("write whats_new.rs");
}

fn file_name(path: &Path) -> &str {
    path.file_name()
        .and_then(|name| name.to_str())
        .unwrap_or_default()
}

/// A strict `X.Y.Z` release version, the only name a release-notes file may have.
fn parse(version: &str, path: &Path) -> (u64, u64, u64) {
    let parts: Vec<u64> = version
        .split('.')
        .map(|part| part.parse().ok())
        .collect::<Option<_>>()
        .unwrap_or_default();
    match parts[..] {
        [major, minor, patch] => (major, minor, patch),
        _ => panic!("{} is not named after an X.Y.Z version", path.display()),
    }
}
