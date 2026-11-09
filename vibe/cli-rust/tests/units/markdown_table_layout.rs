//! Markdown table column widths: fewest rendered rows when the table must shrink.

use vibe_rs::ui::markdown::render;

fn rows(text: &str, width: u16) -> Vec<String> {
    render(text, width)
        .iter()
        .map(|line| {
            line.spans
                .iter()
                .map(|span| span.content.as_ref())
                .collect::<String>()
        })
        .filter(|row| !row.trim().is_empty())
        .collect()
}

#[test]
fn wide_prose_column_gets_the_space_long_tokens_cannot_use() {
    let table = "| Path | Notes |\n| --- | --- |\n\
        | src/very/long/path/to/a/module/file_name.rs | This change updates the parser so that the row is handled correctly and adds a regression test |";
    let rendered = rows(table, 60);
    assert_eq!(rendered.len(), 7, "{rendered:#?}");
    assert!(rendered.iter().all(|row| row.chars().count() <= 60));
}

#[test]
fn whole_words_win_when_the_row_count_ties() {
    let table = "| Owner | Change |\n| --- | --- |\n| engineering-le-chat | adds hooks |";
    let rendered = rows(table, 40);
    assert!(
        rendered
            .iter()
            .any(|row| row.contains("engineering-le-chat")),
        "{rendered:#?}"
    );
}

#[test]
fn tables_that_fit_keep_their_natural_layout() {
    let table = "| A | B |\n| --- | --- |\n| one | two |";
    assert_eq!(rows(table, 40).len(), 5);
}

#[test]
fn narrow_terminals_never_overflow() {
    let table = "| Alpha | Beta | Gamma | Delta |\n| --- | --- | --- | --- |\n| one two three | four five | six | seven eight nine |";
    for width in 8..40 {
        for row in rows(table, width) {
            assert!(row.chars().count() <= width as usize, "{width}: {row}");
        }
    }
}
