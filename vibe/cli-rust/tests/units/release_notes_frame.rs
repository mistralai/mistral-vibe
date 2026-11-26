//! The release-notes frame keeps every row at the transcript width, even when the title does not fit.

use unicode_width::UnicodeWidthStr;
use vibe_rs::ui::markdown;

fn rows(width: u16) -> Vec<String> {
    let border = vibe_rs::ui::theme::md_fence_border();
    markdown::framed(
        "**v1.0.0**\n\n- 宽 note",
        width,
        "Release notes",
        border,
        |_| false,
    )
    .lines()
    .iter()
    .map(|line| line.to_string())
    .collect()
}

#[test]
fn every_row_spans_the_width_at_any_size() {
    for width in [10u16, 16, 19, 40, 120] {
        let rows = rows(width);
        assert!(
            rows[0].starts_with('┌') && rows[0].ends_with('┐'),
            "{rows:?}"
        );
        assert!(rows.last().unwrap().starts_with('└'), "{rows:?}");
        for row in &rows {
            assert_eq!(row.width(), width as usize, "{width}: {row:?}");
        }
    }
}

#[test]
fn a_second_list_stands_one_blank_row_below_the_first() {
    let border = vibe_rs::ui::theme::md_fence_border();
    let rows: Vec<String> =
        markdown::framed("- a\n- b\n\n* c\n", 20, "Release notes", border, |_| false)
            .lines()
            .iter()
            .map(|line| {
                let row = line.to_string();
                row.trim_matches(|c| c == '│' || c == ' ').to_owned()
            })
            .collect();
    let b = rows.iter().position(|row| row.ends_with('b')).unwrap();
    assert_eq!(rows[b + 1], "", "{rows:?}");
    assert!(rows[b + 2].ends_with('c'), "{rows:?}");
}

#[test]
fn the_title_sits_in_the_top_rule() {
    assert!(rows(40)[0].starts_with("┌─ Release notes ─"));
}
