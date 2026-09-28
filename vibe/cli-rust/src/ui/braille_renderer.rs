//! Braille grid renderer: lit dot coordinates to Unicode braille glyphs (U+2800+).

use std::collections::HashSet;

/// Render lit dots as a `\n`-joined braille grid; empty cells are spaces.
pub fn render_braille(dots: &HashSet<(i16, i16)>, width: i16, height: i16) -> String {
    let cols = ((width + 1) / 2) as usize;
    let rows = ((height + 3) / 4) as usize;
    let mut grid = vec![vec![0u8; cols]; rows];

    for &(x, y) in dots {
        if x < 0 || y < 0 {
            continue;
        }
        let (cx, cy) = ((x / 2) as usize, (y / 4) as usize);
        if cy >= rows || cx >= cols {
            continue;
        }
        let (sub_x, sub_y) = (x % 2, y % 4);
        grid[cy][cx] |= 1 << (dot_bit(sub_x, sub_y) - 1);
    }

    grid.iter()
        .map(|row| {
            row.iter()
                .map(|&mask| {
                    if mask == 0 {
                        ' '
                    } else {
                        char::from_u32(0x2800 + mask as u32).unwrap_or(' ')
                    }
                })
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// The braille dot number (1..=8) for a sub-cell offset (`_braille_dot_index`).
fn dot_bit(sub_x: i16, sub_y: i16) -> u8 {
    if sub_y < 3 {
        (sub_y + 1 + 3 * sub_x) as u8
    } else {
        (7 + sub_x) as u8
    }
}
