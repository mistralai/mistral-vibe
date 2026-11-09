//! The hint vocabulary lays out `key action` pairs two spaces apart and measures them in columns.

use vibe_rs::hints::{self, action, key};

#[test]
fn runs_pair_keys_and_actions_with_the_renderer_spacing() {
    let runs = hints::runs(&[hints::NAVIGATE, (key::ESC, action::CLOSE)]);
    let text: String = runs.iter().map(|(text, _)| *text).collect();
    assert_eq!(text, "↑↓/jk navigate  Esc close");
    let keys: Vec<&str> = runs
        .iter()
        .filter(|(_, is_key)| *is_key)
        .map(|(text, _)| *text)
        .collect();
    assert_eq!(keys, [key::NAV, key::ESC]);
}

#[test]
fn width_counts_terminal_columns() {
    assert_eq!(hints::width(&[]), 0);
    assert_eq!(
        hints::width(&[hints::NAVIGATE]),
        "↑↓/jk navigate".len() as u16 - 4
    );
    assert_eq!(hints::width(&[hints::SELECT, hints::CANCEL]), 24);
}
