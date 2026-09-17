//! Unified-diff hunks pinned to Python difflib fixtures, plus diff helpers.

use vibe_rs::server::FileEditEffectOutput;
use vibe_rs::ui::transcript::{
    gutter_width, language, occurrences, render_edit_diff, unified_diff, DiffOccurrence, Hunk,
};

/// `(prefix, text)` rows of a hunk, as Python `unified_diff` lines carry them.
fn rows(hunk: &Hunk) -> Vec<(char, &str)> {
    hunk.rows
        .iter()
        .map(|(prefix, line)| (*prefix, line.as_str()))
        .collect()
}

#[test]
fn identical_inputs_produce_no_hunks() {
    assert!(unified_diff(&["one", "two", "three"], &["one", "two", "three"], 2).is_empty());
    assert!(unified_diff(&[], &[], 2).is_empty());
}

#[test]
fn single_line_change_keeps_two_context_lines() {
    let hunks = unified_diff(
        &["one", "two", "three", "four", "five"],
        &["one", "two", "THREE", "four", "five"],
        2,
    );
    assert_eq!(hunks.len(), 1);
    assert_eq!(hunks[0].old_start, 1);
    assert_eq!(hunks[0].new_start, 1);
    assert_eq!(
        rows(&hunks[0]),
        vec![
            (' ', "one"),
            (' ', "two"),
            ('-', "three"),
            ('+', "THREE"),
            (' ', "four"),
            (' ', "five"),
        ]
    );
}

#[test]
fn all_removed_lines_pair_with_an_empty_new_range() {
    let hunks = unified_diff(&["x", "y", "z"], &[], 2);
    assert_eq!(hunks.len(), 1);
    // Python header: `@@ -1,3 +0,0 @@`.
    assert_eq!(hunks[0].old_start, 1);
    assert_eq!(hunks[0].new_start, 0);
    assert_eq!(rows(&hunks[0]), vec![('-', "x"), ('-', "y"), ('-', "z")]);
}

#[test]
fn all_added_lines_pair_with_an_empty_old_range() {
    let hunks = unified_diff(&[], &["x", "y"], 2);
    assert_eq!(hunks.len(), 1);
    // Python header: `@@ -0,0 +1,2 @@`.
    assert_eq!(hunks[0].old_start, 0);
    assert_eq!(hunks[0].new_start, 1);
    assert_eq!(rows(&hunks[0]), vec![('+', "x"), ('+', "y")]);
}

#[test]
fn inserts_carry_the_old_line_numbering() {
    let hunks = unified_diff(&["a", "b", "c"], &["a", "x", "b", "c"], 2);
    assert_eq!(hunks.len(), 1);
    // Python header: `@@ -1,3 +1,4 @@`.
    assert_eq!(hunks[0].old_start, 1);
    assert_eq!(hunks[0].new_start, 1);
    assert_eq!(
        rows(&hunks[0]),
        vec![(' ', "a"), ('+', "x"), (' ', "b"), (' ', "c")]
    );
}

#[test]
fn distant_changes_split_into_two_hunks() {
    let old: Vec<String> = (1..=10).map(|i| format!("l{i}")).collect();
    let old_refs: Vec<&str> = old.iter().map(String::as_str).collect();
    let new: Vec<String> = ["l1", "L2", "l3", "l4", "l5", "l6", "l7", "l8", "L9", "l10"]
        .iter()
        .map(|s| s.to_string())
        .collect();
    let new_refs: Vec<&str> = new.iter().map(String::as_str).collect();

    let hunks = unified_diff(&old_refs, &new_refs, 2);
    assert_eq!(hunks.len(), 2, "the middle context splits the hunks");
    // Python headers: `@@ -1,4 +1,4 @@` and `@@ -7,4 +7,4 @@`.
    assert_eq!((hunks[0].old_start, hunks[0].new_start), (1, 1));
    assert_eq!((hunks[1].old_start, hunks[1].new_start), (7, 7));
    assert_eq!(
        rows(&hunks[0]),
        vec![
            (' ', "l1"),
            ('-', "l2"),
            ('+', "L2"),
            (' ', "l3"),
            (' ', "l4"),
        ]
    );
    assert_eq!(
        rows(&hunks[1]),
        vec![
            (' ', "l7"),
            (' ', "l8"),
            ('-', "l9"),
            ('+', "L9"),
            (' ', "l10"),
        ]
    );
}

fn occurrence(old: &str, new: &str, start_line: Option<u32>) -> DiffOccurrence {
    DiffOccurrence {
        start_line,
        old_lines: old.into(),
        new_lines: new.into(),
    }
}

#[test]
fn occurrences_fall_back_to_the_bare_strings() {
    let output = FileEditEffectOutput {
        file: "f.rs".into(),
        old_string: "old".into(),
        new_string: "new".into(),
        occurrences: vec![],
    };
    let found = occurrences(&output);
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].start_line, None);
    assert_eq!(found[0].old_lines, "old");
    assert_eq!(found[0].new_lines, "new");
}

#[test]
fn gutter_width_tracks_the_widest_line_number() {
    let one = occurrence("old", "new", Some(3));
    // Line numbers are padded to the minimum width of 5, plus the sign column.
    assert_eq!(gutter_width(&[one]), 2 + 5, "line 3 padded to width 5");

    let wide = occurrence("a\nb\nc", "a\nb\nc", Some(1200));
    assert_eq!(gutter_width(&[wide]), 2 + 5, "1202 still fits width 5");

    let unnumbered = occurrence("old", "new", None);
    assert_eq!(gutter_width(&[unnumbered]), 2, "no numbers, just the sign");
}

#[test]
fn language_reads_the_file_extension() {
    assert_eq!(language("src/main.rs"), "rs");
    assert_eq!(language("notes.md"), "md");
    assert_eq!(language("noext"), "");
}

#[test]
fn render_edit_diff_numbers_rows_from_the_occurrence_offset() {
    let occ = occurrence("def f():\n    pass\n", "def f():\n    return 1\n", Some(10));
    let diff_rows = render_edit_diff(&[occ], "py");
    let texts: Vec<String> = diff_rows
        .iter()
        .map(|row| {
            row.spans
                .iter()
                .map(|span| span.content.clone())
                .collect::<String>()
        })
        .collect();
    let joined = texts.join("\n");
    assert!(joined.contains(" 10 "));
    assert!(joined.contains("return 1"));
    // The @@ header is never rendered; the gutter carries the pairing.
    assert!(!joined.contains("@@"));
}
