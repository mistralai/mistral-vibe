//! Fuzzy scoring, file-mention matching, @-trigger tokenization, text shaping.

use std::collections::BTreeMap;

use vibe_rs::app::App;
use vibe_rs::completion_manager;
use vibe_rs::utils::file_match::matching;
use vibe_rs::utils::fuzzy::score;
use vibe_rs::utils::text::{ellipsize, multi_line, single_line, wrap_hard};

fn paths(entries: &[(&str, bool)]) -> BTreeMap<String, bool> {
    entries
        .iter()
        .map(|(path, dir)| (path.to_string(), *dir))
        .collect()
}

#[test]
fn prefix_outranks_boundary_outranks_subsequence() {
    let prefix = score("ma", "main.rs").expect("prefix match");
    let boundary = score("rs", "foo.rs").expect("word-boundary match after '.'");
    let subsequence = score("os", "frogs").expect("subsequence match");
    assert!(prefix > boundary, "{prefix} > {boundary}");
    assert!(boundary > subsequence, "{boundary} > {subsequence}");
}

#[test]
fn score_handles_empty_unicode_and_unmatched_queries() {
    assert_eq!(score("", "anything"), Some(0), "empty pattern scores zero");
    assert_eq!(score("a", ""), None, "pattern longer than text");
    assert_eq!(score("z", "abc"), None, "no subsequence");
    assert!(
        score("RS", "foo.rs").is_some(),
        "matching is case-insensitive"
    );
    assert!(
        score("café", "café.txt").is_some(),
        "unicode prefix matches"
    );
}

#[test]
fn matching_ranks_exact_filenames_first() {
    let index = paths(&[
        ("README.md", false),
        ("src/main.rs", false),
        ("src/other_main.rs", false),
    ]);
    let found = matching(&index, "main.rs", 10);
    assert_eq!(found.first().map(String::as_str), Some("@src/main.rs"));
    assert_eq!(found.len(), 2);
}

#[test]
fn matching_lists_root_entries_for_an_empty_query() {
    let index = paths(&[
        ("README.md", false),
        ("nested/deep.rs", false),
        (".hidden", false),
        ("visible", true),
    ]);
    assert_eq!(
        matching(&index, "", 10),
        vec!["@README.md", "@visible/"],
        "only root entries, dotfiles hidden"
    );
}

#[test]
fn matching_lists_immediate_children_of_a_path_prefix() {
    let index = paths(&[
        ("src", true),
        ("src/main.rs", false),
        ("src/util", true),
        ("src/util/deep.rs", false),
        ("other/file.rs", false),
    ]);
    let found = matching(&index, "src/", 10);
    assert_eq!(found, vec!["@src/main.rs", "@src/util/"]);
}

#[test]
fn matching_falls_back_to_fuzzy_when_the_prefix_has_no_children() {
    let index = paths(&[("src/main.rs", false)]);
    assert_eq!(matching(&index, "docs/", 10), Vec::<String>::new());
}

#[test]
fn matching_ties_break_alphabetically() {
    let index = paths(&[("z.rs", false), ("a.rs", false)]);
    assert_eq!(matching(&index, ".rs", 10), vec!["@a.rs", "@z.rs"]);
}

#[test]
fn matching_respects_the_limit() {
    let index = paths(&[("a.txt", false), ("b.txt", false), ("c.txt", false)]);
    assert_eq!(matching(&index, "", 2).len(), 2);
}

#[test]
fn at_trigger_tokenizes_on_the_last_mention() {
    let mut app = App::default();
    app.chat_input.input = "look at @src/ma".into();
    assert!(completion_manager::active_is_file(&app));

    app.chat_input.input = "look at @src then more words".into();
    assert!(!completion_manager::active_is_file(&app), "space closes it");

    app.chat_input.input = "no mention here".into();
    assert!(!completion_manager::active_is_file(&app));

    app.chat_input.input = "@a@b".into();
    assert!(completion_manager::active_is_file(&app), "last @ wins");

    app.chat_input.input = "/command".into();
    assert!(
        !completion_manager::active_is_file(&app),
        "slash is not a file"
    );
}

#[test]
fn single_line_flattens_whitespace_runs_and_drops_escapes() {
    assert_eq!(
        single_line("for f in *.py; do\n  echo\ndone"),
        "for f in *.py; do echo done"
    );
    assert_eq!(single_line(" \tpadded \x1b[31m "), "padded [31m");
}

#[test]
fn multi_line_keeps_newlines_and_normalises_carriage_returns() {
    assert_eq!(multi_line("a\r\nb\rc"), "a\nb\nc");
    assert_eq!(multi_line("a\n\tb\x1bc"), "a\n\tbc");
}

#[test]
fn ellipsize_counts_cells_not_chars() {
    assert_eq!(ellipsize("abcdef", 6), "abcdef");
    assert_eq!(ellipsize("abcdef", 4), "abc…");
    assert_eq!(ellipsize("abcdef", 0), "");
    assert_eq!(ellipsize("日本語です", 5), "日本…");
}

#[test]
fn wrap_hard_hangs_continuations_and_cuts_unbreakable_runs() {
    assert_eq!(wrap_hard("  echo one two", 8), vec!["  echo", "one two"]);
    assert_eq!(wrap_hard("  short", 8), vec!["  short"]);
    assert_eq!(
        wrap_hard("/some/long/path", 6),
        vec!["/some/", "long/p", "ath"]
    );
    assert_eq!(wrap_hard("日本語です", 4), vec!["日本", "語で", "す"]);
}
