//! Output buffering: cleaning, split characters, tabs, the byte cap, line numbers and the wrap.

use super::{Output, KEEP_BYTES};

fn output() -> Output {
    Output::new("process-0".to_owned(), "make watch".to_owned(), 1)
}

fn texts(output: &Output) -> Vec<&str> {
    output.rows().iter().map(|row| output.text(row)).collect()
}

#[test]
fn cleans_escapes_and_redrawn_lines_and_drops_trailing_blanks() {
    let mut output = output();
    output.append(b"\x1b[32mok\x1b[0m\nprogress 10%\rprogress 100%\n\n");
    assert_eq!(output.lines(), ["ok", "progress 100%"]);
}

#[test]
fn joins_a_character_split_across_pages() {
    let mut output = output();
    let text = "café\n".as_bytes();
    output.append(&text[..4]);
    output.append(&text[4..]);
    assert_eq!(output.lines(), ["café"]);
}

#[test]
fn a_line_written_across_pages_is_cleaned_whole() {
    let mut output = output();
    output.append(b"done\nprogress 10%\r\x1b[3");
    output.append(b"2mprogress 100%\x1b[0m\n");
    assert_eq!(output.lines(), ["done", "progress 100%"]);
}

#[test]
fn expands_tabs_so_columns_keep_their_width() {
    let mut output = output();
    output.append(b"col1\tvalue\t1\n");
    assert_eq!(output.lines(), ["col1    value   1"]);
    output.wrap(10);
    assert_eq!(texts(&output), ["col1   ", "value   1"]);
}

#[test]
fn keeps_the_latest_bytes_from_a_line_start() {
    let mut output = output();
    let line = "x".repeat(99) + "\n";
    let page = line.repeat(KEEP_BYTES / 100 + 10);
    output.append(page.as_bytes());
    assert!(output.bytes.len() <= KEEP_BYTES);
    assert!(output.lines().iter().all(|row| row.len() == 99));
    assert_eq!(
        output.first_line() + output.lines().len(),
        KEEP_BYTES / 100 + 10
    );
}

#[test]
fn a_cut_right_at_a_line_start_keeps_that_line() {
    let mut output = output();
    output.append(b"old\n");
    output.append(&[b'x'; KEEP_BYTES]);
    assert_eq!(output.first_line(), 1, "only the old line went");
    assert_eq!(output.lines()[0].len(), KEEP_BYTES);
}

#[test]
fn rewraps_when_the_width_changes_and_wraps_only_new_lines_on_a_page() {
    let mut output = output();
    output.append(b"abcdef\n");
    assert!(!output.wrap(3), "a first wrap replaces nothing");
    assert_eq!(texts(&output), ["abc", "def"]);
    assert!(output.wrap(6));
    assert_eq!(texts(&output), ["abcdef"]);
    output.append(b"gh");
    output.append(b"ij\n");
    assert_eq!(texts(&output), ["abcdef", "ghij"]);
}

#[test]
fn reports_the_wrapped_rows_the_cap_dropped() {
    let mut output = output();
    let line = "x".repeat(99) + "\n";
    output.append(line.repeat(KEEP_BYTES / 100).as_bytes());
    output.wrap(50);
    let appended = output.append(line.repeat(10).as_bytes());
    assert!(appended.rows >= 20, "each line wrapped to 2: {appended:?}");
    assert_eq!(appended.rows % 2, 0);
    assert_eq!(output.rows()[0].line, output.first_line());
}

#[test]
fn a_line_longer_than_the_cap_keeps_whole_characters_and_takes_a_new_number() {
    let mut output = output();
    let appended = output.append("€".repeat(KEEP_BYTES / 3 + 10).as_bytes());
    assert!(appended.mid_line);
    assert!(!output.lines()[0].contains('\u{FFFD}'));
    assert_eq!(output.first_line(), 1, "its old bytes no longer hold");
    let cut = output.append(b"yyy");
    assert!(cut.mid_line);
    assert_eq!(output.first_line(), 2);
}

#[test]
fn redraws_of_a_progress_line_keep_the_lines_above() {
    let mut output = output();
    output.append(b"error: disk almost full\nbuilding\n");
    for percent in 0..10_000 {
        output.append(format!("\rdownloading {:>3}%", percent % 100).as_bytes());
    }
    assert_eq!(
        output.lines(),
        ["error: disk almost full", "building", "downloading  99%"]
    );
}

#[test]
fn a_long_tabbed_line_expands_in_linear_time() {
    let mut output = output();
    let page = "ab\t".repeat(64_000 / 3);
    let started = crate::clock::real_now();
    output.append(page.as_bytes());
    output.append(page.as_bytes());
    let took = crate::clock::real_now().duration_since(started);
    assert!(
        took < std::time::Duration::from_secs(2),
        "two pages took {took:?}"
    );
}

#[test]
fn a_line_longer_than_the_cap_that_ends_keeps_its_tail() {
    let mut output = output();
    let mut page = vec![b'x'; KEEP_BYTES + 10];
    page.push(b'\n');
    let appended = output.append(&page);
    assert!(appended.mid_line);
    assert_eq!(output.lines().len(), 1);
    assert!(output.lines()[0].len() >= KEEP_BYTES - 1);
}
