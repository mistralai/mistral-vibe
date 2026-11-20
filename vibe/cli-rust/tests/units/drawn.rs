//! Reading values back off a rendered screen.

/// A detail value as drawn from `start`: its row and the rows folded under it with nothing in the label column, joined, box borders taken out.
pub fn drawn_value(screen: &[String], start: &str) -> String {
    let first = screen
        .iter()
        .position(|row| row.contains(start))
        .expect("the value is drawn");
    let column = screen[first].find(start).unwrap();
    let at = screen[first][..column].chars().count();
    let label_column = |row: &String| row.chars().take(at).collect::<String>();
    let mut rows = screen[first..].iter();
    let head = rows.next().into_iter();
    let folds = rows.take_while(|row| label_column(row).trim_matches([' ', '│']).is_empty());
    head.chain(folds)
        .map(|row| row.chars().skip(at).collect::<String>())
        .map(|tail| tail.trim_end_matches([' ', '│']).to_owned())
        .take_while(|tail| !tail.is_empty())
        .collect()
}
