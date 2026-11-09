//! Color values are spelled only in `src/ui/theme/`, and the list cursor's colors only through `ui/list_cursor.rs`.

use std::fs;
use std::path::Path;

/// A ratatui color constructor other than `Reset`, or an SGR color escape.
fn spells_a_color(line: &str) -> bool {
    const NAMES: [&str; 18] = [
        "Rgb(",
        "Indexed(",
        "Black",
        "White",
        "Red",
        "Green",
        "Yellow",
        "Blue",
        "Magenta",
        "Cyan",
        "Gray",
        "DarkGray",
        "LightRed",
        "LightGreen",
        "LightYellow",
        "LightBlue",
        "LightMagenta",
        "LightCyan",
    ];
    let ratatui = NAMES
        .iter()
        .any(|name| line.contains(&format!("Color::{name}")));
    let sgr = line.match_indices("\\x1b[").any(|(at, _)| {
        let code = &line[at + 5..];
        let code = code.trim_start_matches(['1', ';']);
        code.starts_with('3') || code.starts_with('9')
    });
    ratatui || sgr
}

fn visit(dir: &Path, offenders: &mut Vec<String>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        let name = path.to_string_lossy().replace('\\', "/");
        let exempt = name.ends_with("/src/ui/theme")
            // Converts any `Color` to SGR; it names no color of its own.
            || name.ends_with("/src/update_prompt/ansi.rs")
            || name.ends_with("/tests.rs")
            || name.ends_with("/tests");
        if exempt {
            continue;
        }
        if path.is_dir() {
            visit(&path, offenders);
        } else if name.ends_with(".rs") {
            let text = fs::read_to_string(&path).unwrap();
            for (index, line) in text.lines().enumerate() {
                if spells_a_color(line) {
                    offenders.push(format!("{name}:{}: {}", index + 1, line.trim()));
                }
            }
        }
    }
}

#[test]
fn colors_are_defined_only_in_the_theme_module() {
    let mut offenders = Vec::new();
    visit(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("src"),
        &mut offenders,
    );
    assert!(
        offenders.is_empty(),
        "define these colors in src/ui/theme/ and name them from there:\n{}",
        offenders.join("\n")
    );
}

#[test]
fn list_cursor_colors_go_through_the_list_cursor_module() {
    fn visit(dir: &Path, offenders: &mut Vec<String>) {
        for entry in fs::read_dir(dir).unwrap() {
            let path = entry.unwrap().path();
            let name = path.to_string_lossy().replace('\\', "/");
            if name.ends_with("/src/ui/theme") || name.ends_with("/src/ui/list_cursor.rs") {
                continue;
            }
            if path.is_dir() {
                visit(&path, offenders);
            } else if name.ends_with(".rs") && !name.ends_with("/tests.rs") {
                let text = fs::read_to_string(&path).unwrap();
                if text.contains("block_cursor_bg") || text.contains("block_cursor_fg") {
                    offenders.push(name);
                }
            }
        }
    }
    let mut offenders = Vec::new();
    visit(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("src"),
        &mut offenders,
    );
    assert!(
        offenders.is_empty(),
        "draw list highlights with ui/list_cursor.rs (paint, style, styles):\n{}",
        offenders.join("\n")
    );
}
