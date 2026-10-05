//! `$VIBE_HOME/.env` parsing (Python `load_dotenv_values` + python-dotenv).

use vibe_rs::credentials::dotenv::parse;

#[test]
fn inline_comments_drop_from_unquoted_values() {
    // python-dotenv `parse_unquoted_value` cuts at the first ` #`.
    let parsed = parse("KEY=abc # note\nPLAIN=abc#tag\n");
    assert_eq!(
        parsed,
        vec![
            ("KEY".to_owned(), "abc".to_owned()),
            ("PLAIN".to_owned(), "abc#tag".to_owned()),
        ]
    );
}

#[test]
fn quoted_values_end_at_their_closing_quote() {
    let parsed = parse("A=\"a # b\" # note\nB='x'  # note\n");
    assert_eq!(
        parsed,
        vec![
            ("A".to_owned(), "a # b".to_owned()),
            ("B".to_owned(), "x".to_owned()),
        ]
    );
}

#[test]
fn broken_lines_are_dropped_like_python_dotenv() {
    // Trailing garbage after a closing quote and unclosed quotes both
    // make python-dotenv drop the line.
    assert!(parse("A=\"abc\" garbage\n").is_empty());
    assert!(parse("A=\"abc\n").is_empty());
}

#[test]
fn empty_env_values_do_not_block_the_file_value() {
    // Python `load_dotenv_values` lets a non-empty .env value through
    // when the shell exported the variable empty.
    let parsed = parse("A=\nB=  \nC=set\n");
    assert_eq!(parsed, vec![("C".to_owned(), "set".to_owned())]);
}
