//! Borders keep ratatui's default square corners: no rounded border type or glyph under `src/`.

use std::fs;
use std::path::Path;

fn rounds_a_corner(line: &str) -> bool {
    line.contains("BorderType::Rounded")
        || line.contains("border::ROUNDED")
        || line.contains("line::ROUNDED")
        || ["\\u{256d}", "\\u{256e}", "\\u{256f}", "\\u{2570}"]
            .iter()
            .any(|escape| line.to_ascii_lowercase().contains(escape))
        || line.contains(['╭', '╮', '╰', '╯'])
}

fn visit(dir: &Path, offenders: &mut Vec<String>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        let name = path.to_string_lossy().replace('\\', "/");
        if path.is_dir() {
            visit(&path, offenders);
        } else if name.ends_with(".rs") {
            let text = fs::read_to_string(&path).unwrap();
            for (index, line) in text.lines().enumerate() {
                if rounds_a_corner(line) {
                    offenders.push(format!("{name}:{}: {}", index + 1, line.trim()));
                }
            }
        }
    }
}

#[test]
fn borders_use_square_corners() {
    let mut offenders = Vec::new();
    visit(
        &Path::new(env!("CARGO_MANIFEST_DIR")).join("src"),
        &mut offenders,
    );
    assert!(
        offenders.is_empty(),
        "draw borders with the default square corners:\n{}",
        offenders.join("\n")
    );
}
