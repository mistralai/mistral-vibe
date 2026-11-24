//! Code preview: link targets resolve to files and lines, and files load around their focus.

use std::path::PathBuf;

use ratatui::layout::Rect;
use vibe_rs::code_preview;
use vibe_rs::code_preview::load::{read, Failure};
use vibe_rs::code_preview::target::{parse, Kind, Location};
use vibe_rs::ui::{self, highlight};

use crate::sessions_page_outcomes::picking;
use crate::support::render;

fn location(path: &str, lines: Option<(usize, usize)>, kind: Kind) -> Option<Location> {
    Some(Location {
        path: PathBuf::from(path),
        lines,
        kind,
    })
}

#[test]
fn editor_and_file_links_resolve_to_absolute_paths() {
    let cases = [
        (
            "vscode://file/repo/src/main.rs:12",
            Some((12, 12)),
            Kind::Editor("vscode"),
        ),
        (
            "VSCode://file/repo/src/main.rs:12:5",
            Some((12, 12)),
            Kind::Editor("vscode"),
        ),
        (
            "cursor://file/repo/src/main.rs",
            None,
            Kind::Editor("cursor"),
        ),
        (
            "zed://file/repo/src/main.rs:7",
            Some((7, 7)),
            Kind::Editor("zed"),
        ),
        (
            "file:///repo/src/main.rs#L3-L9",
            Some((3, 9)),
            Kind::FileUrl,
        ),
        (
            "file://localhost/repo/src/main.rs:4",
            Some((4, 4)),
            Kind::FileUrl,
        ),
    ];
    for (url, lines, kind) in cases {
        assert_eq!(
            parse(url, None),
            location("/repo/src/main.rs", lines, kind),
            "{url}"
        );
    }
}

#[test]
fn plain_paths_resolve_against_the_session_directory() {
    let cwd = Some("/repo");
    assert_eq!(
        parse("src/main.rs:4-8", cwd),
        location("/repo/src/main.rs", Some((4, 8)), Kind::Path)
    );
    assert_eq!(
        parse("src/main.rs#L7", cwd),
        location("/repo/src/main.rs", Some((7, 7)), Kind::Path)
    );
    assert_eq!(
        parse("src/my%20file.rs", cwd),
        location("/repo/src/my file.rs", None, Kind::Path)
    );
    assert_eq!(
        parse("Makefile:12", cwd),
        location("/repo/Makefile", Some((12, 12)), Kind::Path)
    );
    assert_eq!(
        parse("main.rs:3:5", cwd),
        location("/repo/main.rs", Some((3, 3)), Kind::Path)
    );
    assert_eq!(parse("src/main.rs", None), None);
}

#[test]
fn web_links_and_anchors_are_not_files() {
    for url in [
        "mailto:user@host",
        "tel:123",
        "slack:open",
        "MAILTO:a@b.c",
        "vscode://fileX/repo/a.rs",
        "file://otherhost/repo/a.rs",
        "https://docs.rs/tokio",
        "#L12",
        "#section",
        "",
    ] {
        assert_eq!(parse(url, Some("/repo")), None, "{url}");
    }
}

#[test]
fn read_keeps_lines_around_the_focus() {
    let dir = tempfile::tempdir().unwrap();
    let text: String = (1..=300).map(|n| format!("line {n}\n")).collect();
    std::fs::write(dir.path().join("big.txt"), text).unwrap();
    let cwd = dir.path().to_str().unwrap();
    let snippet = read(&parse("big.txt:250", Some(cwd)).unwrap(), Some(cwd)).unwrap();
    assert_eq!(snippet.title, "big.txt:250");
    assert_eq!(snippet.first, 150);
    assert_eq!(snippet.focus, Some((250, 250)));
    assert_eq!(snippet.lines.len(), 151);
    assert_eq!(snippet.total, 300);
}

#[test]
fn read_skips_binary_and_missing_files_and_clamps_focus() {
    let dir = tempfile::tempdir().unwrap();
    std::fs::write(dir.path().join("bin.dat"), b"a\0b").unwrap();
    std::fs::write(dir.path().join("short.py"), "x = 1\n\ty = 2\n").unwrap();
    let cwd = dir.path().to_str().unwrap();
    let load = |url: &str| read(&parse(url, Some(cwd)).unwrap(), Some(cwd));
    std::fs::write(dir.path().join("empty.py"), "").unwrap();
    std::fs::create_dir(dir.path().join("src")).unwrap();
    assert_eq!(load("bin.dat").err(), Some(Failure::Binary));
    assert_eq!(load("missing.rs").err(), Some(Failure::NotFound));
    assert_eq!(load("empty.py").err(), Some(Failure::Empty));
    assert_eq!(load("src").err(), Some(Failure::NotFile));
    let snippet = load("short.py:99").unwrap();
    assert_eq!(snippet.focus, Some((2, 2)));
    let second: String = snippet.lines[1]
        .iter()
        .map(|span| span.content.as_ref())
        .collect();
    assert_eq!(second, "    y = 2");
}

#[cfg(unix)]
#[test]
fn a_named_pipe_is_skipped_without_blocking() {
    let dir = tempfile::tempdir().unwrap();
    let pipe = dir.path().join("pipe.rs");
    let status = std::process::Command::new("mkfifo")
        .arg(&pipe)
        .status()
        .unwrap();
    assert!(status.success());
    let cwd = dir.path().to_str().unwrap();
    let read = read(&parse("pipe.rs", Some(cwd)).unwrap(), Some(cwd));
    assert_eq!(read.err(), Some(Failure::NotFile));
}

#[test]
fn a_window_starting_inside_a_docstring_highlights_like_the_whole_file() {
    let dir = tempfile::tempdir().unwrap();
    let body: String = (1..=300).map(|n| format!("y{n} = {n}\n")).collect();
    let text = format!(
        "{}\"\"\"\n{}\"\"\"\n{body}",
        "x = 0\n".repeat(140),
        "doc\n".repeat(20)
    );
    std::fs::write(dir.path().join("f.py"), &text).unwrap();
    let cwd = dir.path().to_str().unwrap();
    let snippet = read(&parse("f.py:250", Some(cwd)).unwrap(), Some(cwd)).unwrap();
    let whole = highlight::block(text.trim_end(), "py").unwrap();
    let row = snippet.row_of(250).unwrap();
    assert_eq!(snippet.first, 150);
    assert_eq!(snippet.lines[row], whole[249]);
}

#[test]
fn a_bottom_app_closes_the_preview_so_esc_reaches_it() {
    let mut app = picking();
    app.code_preview.area = Rect::new(0, 4, 40, 6);
    app.code_preview.shown = true;
    render(120, 30, |frame| ui::draw(&mut app, frame));
    assert!(!app.code_preview.shown);
    assert!(!code_preview::dismiss(&mut app));
}
