//! Sent long pastes stay collapsed through display-content markers.

use std::sync::Arc;

use vibe_rs::collapsed_pastes::{collapse, collapse_marked, CollapsedPastes};

fn pastes(texts: &[&str]) -> CollapsedPastes {
    let mut pastes = CollapsedPastes::default();
    for text in texts {
        pastes.remember(Arc::from(*text));
    }
    pastes
}

#[test]
fn a_marked_paste_collapses_back_to_its_placeholder() {
    let paste = "é\nsecond line";
    let text = format!("before {paste} after");
    let display = pastes(&[paste]).display(&text);

    assert_eq!(
        collapse(&text, display.as_ref()),
        "before [Pasted 13 characters] after"
    );
}

#[test]
fn several_pastes_collapse_in_order_and_unknown_text_stays() {
    let text = "a PASTE-ONE b PASTE-TWO c";
    let display = pastes(&["PASTE-TWO", "PASTE-ONE", "absent"]).display(text);

    assert_eq!(
        collapse(text, display.as_ref()),
        "a [Pasted 9 characters] b [Pasted 9 characters] c"
    );
    assert!(pastes(&["absent"]).display(text).is_none());
}

#[test]
fn a_marker_that_no_longer_matches_leaves_the_text_alone() {
    let display = pastes(&["PASTE"]).display("x PASTE");

    assert_eq!(collapse("x PASTA", display.as_ref()), "x PASTA");
    assert_eq!(collapse("x", display.as_ref()), "x");
    assert_eq!(collapse("x PASTE", None), "x PASTE");
}

#[test]
fn a_paste_shown_in_full_is_no_longer_marked() {
    let mut remembered = pastes(&["PASTE"]);
    remembered.forget("PASTE");

    assert!(remembered.display("x PASTE").is_none());
}

#[test]
fn a_paste_trimmed_at_the_message_edges_is_still_marked() {
    let paste = "    indented code\nsecond line\n";
    let sent = "    indented code\nsecond line";
    let display = pastes(&[paste]).display(sent.trim());

    assert_eq!(
        collapse(sent.trim(), display.as_ref()),
        "[Pasted 30 characters]",
        "the sent message shows the count the composer showed"
    );
}

#[test]
fn every_copy_of_a_paste_sent_several_times_is_marked() {
    let paste = "row 1\nrow 2\n";
    let sent = format!("{paste}\n{paste}\n{paste}");
    let display = pastes(&[paste]).display(sent.trim());

    assert_eq!(
        collapse(sent.trim(), display.as_ref()),
        "[Pasted 12 characters]\n[Pasted 12 characters]\n[Pasted 12 characters]"
    );
}

#[test]
fn collapsing_reports_where_each_placeholder_lands() {
    let text = "é PASTE-ONE\nx PASTE-ONE";
    let display = pastes(&["PASTE-ONE"]).display(text);

    let collapsed = collapse_marked(text, display.as_ref());

    let label = "[Pasted 9 characters]";
    assert_eq!(collapsed.text, format!("é {label}\nx {label}"));
    let lines: Vec<Vec<(&str, bool)>> = collapsed.lines().collect();
    assert_eq!(
        lines,
        [
            vec![("é ", false), (label, true)],
            vec![("x ", false), (label, true)]
        ]
    );
}

#[test]
fn a_truncated_preview_keeps_its_placeholders_after_the_prefix() {
    let text = "see PASTE-ONE then more";
    let display = pastes(&["PASTE-ONE"]).display(text);
    let collapsed = collapse_marked(text, display.as_ref());

    let title = collapsed.truncated("Go: ", 10);

    assert_eq!(title.text, "Go: see [Paste");
    let lines: Vec<Vec<(&str, bool)>> = title.lines().collect();
    assert_eq!(lines, [vec![("Go: see ", false), ("[Paste", true)]]);
    assert_eq!(collapsed.truncated("", 100), collapsed);
}
