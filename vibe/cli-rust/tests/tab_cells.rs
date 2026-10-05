//! Tab stops in composer cells follow Textual's TextArea (4 columns from the line start).

use vibe_rs::ui::tab_cells::{cells, cells_width, Cell};

fn widths(text: &str, column: usize) -> Vec<usize> {
    cells(text, column).map(|cell| cell.width).collect()
}

#[test]
fn tab_reaches_the_next_stop_from_its_column() {
    assert_eq!(widths("\ta", 0), [4, 1]);
    assert_eq!(widths("ab\tc", 0), [1, 1, 2, 1]);
    assert_eq!(widths("abcd\t", 0), [1, 1, 1, 1, 4]);
    assert_eq!(widths("\t", 3), [1]);
    assert_eq!(widths("界\t", 0), [2, 2]);
}

#[test]
fn tab_draws_as_spaces_covering_its_width() {
    let shown: String = cells("a\tb", 0).map(Cell::shown).collect();
    assert_eq!(shown, "a   b");
    assert_eq!(cells_width("a\tb", 0), 5);
}
