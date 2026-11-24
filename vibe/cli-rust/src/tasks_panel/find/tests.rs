//! Matches in a task's output, and stepping between them like a pager.

use super::{find, Find};
use crate::tasks_panel::Output;

fn lines(lines: &[&str]) -> Vec<String> {
    lines.iter().map(|line| (*line).to_owned()).collect()
}

fn output(bytes: &[u8]) -> Output {
    let mut output = Output::new("p".to_owned(), "make".to_owned(), 1);
    output.append(bytes);
    output
}

#[test]
fn finds_every_match_ignoring_case_numbered_from_the_first_line() {
    let hits = find(&lines(&["error ok error", "ok", "ERROR"]), 5, "error");
    assert_eq!(hits, [(5, 0), (5, 9), (7, 0)]);
}

#[test]
fn steps_from_the_last_match_and_wraps_both_ways() {
    let output = output(b"err one\nok\nerr two\n");
    let mut found = Find::default();
    assert!(found.set_query("ERR", &output));
    assert!(
        !found.set_query("ERR", &output),
        "the same search changes nothing"
    );
    assert_eq!(
        found.step(false),
        Some((2, 0)),
        "a new search starts at the latest match"
    );
    assert_eq!(found.step(true), Some((0, 0)), "next wraps to the first");
    assert_eq!(
        found.step(false),
        Some((2, 0)),
        "previous wraps to the last"
    );
    assert_eq!(found.position(), (Some(2), 2));
}

#[test]
fn the_current_match_holds_across_a_rewrap() {
    let mut output = output(b"aaaaaaaaaaaaaaaaaaaa err one\nok\nerr two\n");
    let mut found = Find::default();
    found.set_query("err", &output);
    found.step(false);
    let current = found.step(true).unwrap();
    assert_eq!(current, (0, 21));
    output.wrap(40);
    assert_eq!(output.row_of(current), Some(0));
    output.wrap(10);
    assert_eq!(
        output.row_of(current),
        Some(2),
        "the same match, wrapped lower"
    );
    assert_eq!(found.position(), (Some(1), 2));
}

#[test]
fn a_page_searches_the_line_it_finished_and_its_new_lines() {
    let mut output = output(b"err one\npartial e");
    let mut found = Find::default();
    found.set_query("err", &output);
    let appended = output.append(b"rr\nerr three\n");
    found.appended(&output, appended.from);
    assert_eq!(found.hits(), [(0, 0), (1, 8), (2, 0)]);
}

#[test]
fn a_character_lowercasing_to_more_bytes_keeps_the_other_matches() {
    let hits = find(&lines(&["\u{130}stanbul ERROR \u{212a}"]), 0, "error");
    assert_eq!(hits, [(0, 10)]);
}
