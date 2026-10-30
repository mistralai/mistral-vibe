//! Regression: a long cwd clipped the bottom bar's token counter.

use vibe_rs::app::App;

use crate::support::bottom_bar_row;

const WIDTH: u16 = 120;

#[test]
fn a_long_cwd_is_ellipsized_and_keeps_the_token_counter() {
    let cwd = format!("/tmp/{}", "x".repeat(100));
    let mut app = App::default();
    app.session.cwd = Some(cwd);
    app.session.tokens = (0, 600_000);

    let row = bottom_bar_row(&mut app, WIDTH);

    let counter = "0/600k tokens (0%)";
    let path_width = WIDTH as usize - 1 - counter.len();
    let head = format!("/tmp/{}", "x".repeat(path_width - "/tmp/…".chars().count()));
    assert_eq!(row, format!("{head}… {counter}"));
}

#[test]
fn a_short_cwd_is_left_untouched() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);

    let row = bottom_bar_row(&mut app, WIDTH);

    assert!(row.starts_with("/tmp/project "));
    assert!(row.ends_with("0/600k tokens (0%)"));
}
