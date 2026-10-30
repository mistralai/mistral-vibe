//! The release-notes frame keeps every row at the transcript width, even when the title does not fit.

use unicode_width::UnicodeWidthStr;
use vibe_rs::ui::markdown;

fn rows(width: u16) -> Vec<String> {
    markdown::framed("**v1.0.0**\n\n- 宽 note", width, "Release notes", |_| {
        false
    })
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
fn the_title_sits_in_the_top_rule() {
    assert!(rows(40)[0].starts_with("┌─ Release notes ─"));
}
