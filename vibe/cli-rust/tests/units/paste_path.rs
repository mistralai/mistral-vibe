//! Image paths pasted or dropped into the composer: detection, `@` mentions, placeholders.

use std::fs;
use std::path::{Path, PathBuf};

use tempfile::TempDir;
use vibe_rs::app::App;
use vibe_rs::input;
use vibe_rs::paste_path::{
    contains_image_path_mention, has_supported_path_root, image_path_mention_on, pasted_image_path,
    pasted_image_path_on,
};

use crate::support::type_text;

/// The `@` mention a lone pasted image path stands for, or the paste unchanged.
fn maybe_prepend_at_for_image_path(pasted: &str) -> String {
    maybe_prepend_at_for_image_path_on(pasted, cfg!(windows))
}

fn maybe_prepend_at_for_image_path_on(pasted: &str, windows: bool) -> String {
    pasted_image_path_on(pasted, windows).map_or_else(
        || pasted.to_owned(),
        |path| image_path_mention_on(&path, windows),
    )
}

struct TestDir(TempDir);

impl TestDir {
    fn new() -> Self {
        Self(tempfile::tempdir().expect("create test directory"))
    }

    fn image(&self, name: &str) -> PathBuf {
        let path = self.0.path().join(name);
        fs::write(&path, b"image fixture").expect("write image fixture");
        path
    }
}

fn quoted(path: &Path) -> String {
    format!("@'{}'", path.display())
}

#[test]
fn rewrites_a_pasted_absolute_image_path() {
    let dir = TestDir::new();
    let image = dir.image("shot.png");

    assert_eq!(
        maybe_prepend_at_for_image_path(&image.display().to_string()),
        format!("@{}", image.display())
    );
}

#[test]
fn normalizes_quotes_and_escaped_spaces() {
    let dir = TestDir::new();
    let image = dir.image("has space.JPG");
    let raw = image.display().to_string();

    assert_eq!(
        maybe_prepend_at_for_image_path(&format!("'{raw}'")),
        quoted(&image)
    );
    assert_eq!(
        maybe_prepend_at_for_image_path(&raw.replace(' ', "\\ ")),
        quoted(&image)
    );
}

#[test]
fn leaves_non_images_relative_paths_and_existing_mentions_unchanged() {
    let dir = TestDir::new();
    let image = dir.image("shot.png");
    let text = dir.0.path().join("notes.md");
    fs::write(&text, "notes").expect("write text fixture");

    assert_eq!(
        maybe_prepend_at_for_image_path(&text.display().to_string()),
        text.display().to_string()
    );
    assert_eq!(maybe_prepend_at_for_image_path("shot.png"), "shot.png");
    let mention = format!("@{}", image.display());
    assert_eq!(maybe_prepend_at_for_image_path(&mention), mention);
}

#[test]
fn a_multiline_paste_with_prose_is_no_image() {
    let dir = TestDir::new();
    let image = dir.image("shot.png");
    let pasted = format!("{}\nother line", image.display());

    assert_eq!(maybe_prepend_at_for_image_path(&pasted), pasted);
}

#[test]
fn detects_image_paths_without_probing_the_filesystem() {
    let missing = "/definitely-missing-vibe/image.png";

    assert_eq!(
        maybe_prepend_at_for_image_path(missing),
        format!("@{missing}")
    );
}

#[test]
fn inserts_numbered_image_placeholders_with_token_boundaries() {
    let mut app = App::default();
    app.chat_input.input = "inspectnow".to_owned();
    app.chat_input.cursor = "inspect".len();

    input::handle_paste(&mut app, "/tmp/diagram.png".to_owned());

    assert_eq!(app.chat_input.input, "inspect [Image #1] now");
    assert_eq!(app.chat_input.cursor, "inspect [Image #1] ".len());

    app.chat_input.input = "inspect".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();
    input::handle_paste(&mut app, "/tmp/diagram.png".to_owned());

    assert_eq!(app.chat_input.input, "inspect [Image #2] ");
    assert_eq!(pasted_image_path("inspect /tmp/diagram.png"), None);
}

fn typed(text: &str) -> String {
    let mut app = App::default();
    type_text(&mut app, text);
    app.chat_input.input
}

#[test]
fn typed_image_paths_stay_as_typed() {
    for text in [
        "open /tmp/vibe/shot.png",
        "'/tmp/vibe/has space.png'",
        "rename /tmp/vibe/shot.png.bak please",
    ] {
        assert_eq!(typed(text), text);
    }
}

#[test]
fn recognizes_windows_drive_and_unc_roots_without_host_dependent_paths() {
    let drive = r"C:\Users\Alice\shot.png";
    let slash_drive = "D:/Pictures/shot.png";
    let unc = r"\\server\share\shot.png";

    assert!(has_supported_path_root(drive, true));
    assert!(has_supported_path_root(slash_drive, true));
    assert!(has_supported_path_root(unc, true));
    assert!(!has_supported_path_root(drive, false));
    assert!(!has_supported_path_root(unc, false));
    assert_eq!(
        maybe_prepend_at_for_image_path_on(drive, true),
        format!("@'{drive}'")
    );
    assert_eq!(
        maybe_prepend_at_for_image_path_on(slash_drive, true),
        format!("@'{slash_drive}'")
    );
    assert_eq!(
        maybe_prepend_at_for_image_path_on(unc, true),
        format!("@{unc}")
    );
}

#[test]
fn recognizes_only_complete_platform_correct_image_mentions() {
    let drive = r"C:\Users\Alice\shot.png";
    let drive_mention = image_path_mention_on(drive, true);
    let unc = r"\\server\share\shot.png";
    let unc_mention = image_path_mention_on(unc, true);

    assert_eq!(drive_mention, format!("@'{drive}'"));
    assert_eq!(
        image_path_mention_on("/tmp/it's.png", false),
        "@\"/tmp/it's.png\"",
    );
    assert!(contains_image_path_mention(
        &format!("inspect {drive_mention}"),
        drive,
    ));
    assert!(contains_image_path_mention(
        "inspect @diagram.png",
        "diagram.png",
    ));
    assert!(!contains_image_path_mention(
        &format!("inspect {unc_mention}x"),
        unc,
    ));
    assert!(!contains_image_path_mention(
        &format!("email{drive_mention}"),
        drive,
    ));
}
