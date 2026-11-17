//! Non-ASCII `@` file matching: accents, case folding, graphemes, camelCase bonus.

use std::collections::BTreeMap;

use vibe_rs::utils::file_match::matching;

fn paths(entries: &[(&str, bool)]) -> BTreeMap<String, bool> {
    entries
        .iter()
        .map(|(path, dir)| (path.to_string(), *dir))
        .collect()
}

#[test]
fn a_query_with_a_combining_accent_matches_the_same_path() {
    let index = paths(&[("cafe\u{301}.md", false), ("other.md", false)]);
    assert_eq!(matching(&index, "cafe\u{301}", 10), vec!["@cafe\u{301}.md"]);
}

fn non_ascii_tree() -> BTreeMap<String, bool> {
    paths(&[
        ("measures/durée_µs.csv", false),
        ("docs/क्षेत्र.pdf", false),
        ("notes/ς.md", false),
        ("pl/Ćwiczenia.md", false),
        ("de/straße.txt", false),
        ("README.md", false),
    ])
}

#[test]
fn a_non_ascii_query_matches_its_exact_substring() {
    let index = non_ascii_tree();
    for (query, expected) in [
        ("durée_µs", "@measures/durée_µs.csv"),
        ("µs", "@measures/durée_µs.csv"),
        ("durée_μs", "@measures/durée_µs.csv"),
        ("ς", "@notes/ς.md"),
        ("Σ", "@notes/ς.md"),
    ] {
        assert_eq!(matching(&index, query, 10), vec![expected], "{query}");
    }
}

#[test]
fn an_uppercase_accented_query_matches_its_path() {
    let index = non_ascii_tree();
    for (query, expected) in [
        ("Ć", "@pl/Ćwiczenia.md"),
        ("Ćwiczenia", "@pl/Ćwiczenia.md"),
        ("ĆWICZENIA", "@pl/Ćwiczenia.md"),
        ("ćwiczenia", "@pl/Ćwiczenia.md"),
        ("ẞ", "@de/straße.txt"),
        ("DURÉE", "@measures/durée_µs.csv"),
        ("README", "@README.md"),
    ] {
        assert_eq!(matching(&index, query, 10), vec![expected], "{query}");
    }
}

#[test]
fn a_camel_case_query_ranks_a_non_ascii_path_by_its_word_starts() {
    for (query, word_starts, scattered) in [
        ("sm", "src/étatMachine.rs", "src/xsxxm.rs"),
        ("fe", "src/fooÉcran.rs", "src/fxexx.rs"),
        ("fé", "src/fooÉcran.rs", "src/fxéxx.rs"),
    ] {
        let index = paths(&[(word_starts, false), (scattered, false)]);
        assert_eq!(
            matching(&index, query, 10),
            vec![format!("@{word_starts}"), format!("@{scattered}")],
            "{query}"
        );
    }
}

#[test]
fn a_query_starting_inside_a_grapheme_matches() {
    let index = non_ascii_tree();
    for query in ["ष", "क्षेत्र"] {
        assert_eq!(
            matching(&index, query, 10),
            vec!["@docs/क्षेत्र.pdf"],
            "{query}"
        );
    }
}

#[test]
fn an_unaccented_query_matches_an_accented_path() {
    let index = non_ascii_tree();
    for (query, expected) in [
        ("duree", "@measures/durée_µs.csv"),
        ("cwiczenia", "@pl/Ćwiczenia.md"),
    ] {
        assert_eq!(matching(&index, query, 10), vec![expected], "{query}");
    }
}

#[test]
fn a_letter_without_an_ascii_form_is_not_stripped() {
    let index = non_ascii_tree();
    assert_eq!(matching(&index, "duree_us", 10), Vec::<String>::new());
}
