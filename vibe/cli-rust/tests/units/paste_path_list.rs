//! Pasted or dropped path lists: splitting, escapes, and one placeholder or mention per path.

use std::path::Path;

use vibe_rs::app::App;
use vibe_rs::input;
use vibe_rs::paste_path::{
    image_path_mention_on, pasted_image_paths_on, path_candidates_on, paths_resolve_on,
    rewrite_bare_image_paths_in_text_on, MAX_PASTED_PATHS,
};

/// The `@` mentions an image-only pasted list stands for, or the paste unchanged.
fn image_list_mentions_on(pasted: &str, windows: bool) -> String {
    pasted_image_paths_on(pasted, windows).map_or_else(
        || pasted.to_owned(),
        |paths| {
            let mentions: Vec<String> = paths
                .iter()
                .map(|path| image_path_mention_on(path, windows))
                .collect();
            mentions.join(" ")
        },
    )
}

#[test]
fn splits_shell_escaped_image_lists_from_finder_paste_and_drop() {
    let pasted = r"/tmp/shot\ \(1\).png /tmp/it\'s.jpg /tmp/b.png";

    assert_eq!(
        image_list_mentions_on(pasted, false),
        "@'/tmp/shot (1).png' @\"/tmp/it's.jpg\" @/tmp/b.png"
    );
}

#[test]
fn keeps_non_ascii_spaces_inside_escaped_screenshot_names() {
    let pasted = "/tmp/Screenshot\\ at\\ 10.00\u{202f}PM.png /tmp/b.png";

    assert_eq!(
        image_list_mentions_on(pasted, false),
        "@'/tmp/Screenshot at 10.00\u{202f}PM.png' @/tmp/b.png"
    );
}

#[test]
fn splits_newline_delimited_image_lists() {
    assert_eq!(
        image_list_mentions_on("/tmp/a.png\n'/tmp/b c.png'\n", false),
        "@/tmp/a.png @'/tmp/b c.png'"
    );
}

#[test]
fn falls_back_to_one_path_with_raw_spaces() {
    assert_eq!(
        image_list_mentions_on("/Users/me/Screen Shot.png", false),
        "@'/Users/me/Screen Shot.png'"
    );
}

#[test]
fn mixed_lists_mention_every_existing_path() {
    let candidates = path_candidates_on("/tmp/doc.pdf /tmp/a.png /tmp/dir", false);
    let exists = |path: &Path| path != Path::new("/tmp/missing.md");

    assert_eq!(
        candidates,
        ["/tmp/doc.pdf", "/tmp/a.png", "/tmp/dir"].map(str::to_owned)
    );
    assert!(paths_resolve_on(&candidates, false, exists));
    assert!(!paths_resolve_on(&candidates, false, |_| false));
    let with_missing = path_candidates_on("/tmp/a.png /tmp/missing.md", false);
    assert!(!paths_resolve_on(&with_missing, false, exists));
    let image_last = "/tmp/doc.pdf /tmp/a.png";
    assert_eq!(image_list_mentions_on(image_last, false), image_last);
}

#[test]
fn prose_and_relative_paths_are_not_path_lists() {
    assert!(path_candidates_on("look at /tmp/a.png", false).is_empty());
    assert!(path_candidates_on("src/a.png docs/b.png", false).is_empty());
    assert!(path_candidates_on("   ", false).is_empty());
}

#[test]
fn windows_lists_keep_backslashes_literal() {
    let pasted = r"C:\Users\Alice\a.png \\server\share\b.png";

    assert_eq!(
        image_list_mentions_on(pasted, true),
        r"@'C:\Users\Alice\a.png' @\\server\share\b.png"
    );
}

#[test]
fn pasted_image_lists_become_padded_placeholders() {
    let mut app = App::default();
    app.chat_input.input = "compare".to_owned();
    app.chat_input.cursor = app.chat_input.input.len();

    input::handle_paste(&mut app, "/tmp/a.png /tmp/b\\ c.png".to_owned());

    assert_eq!(app.chat_input.input, "compare [Image #1] [Image #2] ");
}

#[test]
fn other_unicode_spaces_still_end_typed_image_paths() {
    for space in ['\u{a0}', '\u{3000}'] {
        assert_eq!(
            rewrite_bare_image_paths_in_text_on(&format!("/tmp/a.png{space}note"), false),
            format!("@/tmp/a.png{space}note")
        );
    }
}

#[test]
fn oversized_path_lists_stay_raw_text() {
    let paths = |count: usize| {
        (0..count)
            .map(|index| format!("/tmp/{index}.png"))
            .collect::<Vec<_>>()
            .join("\n")
    };

    assert_eq!(
        path_candidates_on(&paths(MAX_PASTED_PATHS), false).len(),
        MAX_PASTED_PATHS
    );
    assert!(path_candidates_on(&paths(MAX_PASTED_PATHS + 1), false).is_empty());
}

#[test]
fn bare_roots_never_become_mentions() {
    for pasted in ["/", "//", "~", "~/"] {
        let candidates = path_candidates_on(pasted, false);
        assert!(!paths_resolve_on(&candidates, false, |_| true));
    }
}

#[test]
fn quoted_lines_keep_their_content_literal() {
    assert_eq!(
        path_candidates_on("'/tmp/a\\b.png'\n/tmp/My Docs/c.png", false),
        vec![r"/tmp/a\b.png".to_owned(), "/tmp/My Docs/c.png".to_owned()]
    );
}
