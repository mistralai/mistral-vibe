//! Diff line-number progression shared by rendering paths.

pub(super) fn next_line_number(
    prefix: char,
    old_lineno: &mut usize,
    new_lineno: &mut usize,
) -> usize {
    match prefix {
        '-' => {
            let lineno = *old_lineno;
            *old_lineno += 1;
            lineno
        }
        '+' => {
            let lineno = *new_lineno;
            *new_lineno += 1;
            lineno
        }
        _ => {
            let lineno = *new_lineno;
            *old_lineno += 1;
            *new_lineno += 1;
            lineno
        }
    }
}
