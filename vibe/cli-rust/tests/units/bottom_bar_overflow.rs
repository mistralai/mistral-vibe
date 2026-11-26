//! Regression: a long cwd clipped the bottom bar's context gauge; it now keeps its end.

use vibe_rs::app::App;

use crate::support::bottom_bar_row;

const WIDTH: u16 = 120;

#[test]
fn a_long_cwd_keeps_its_end_and_the_context_gauge() {
    let cwd = format!("/tmp/{}", "x".repeat(110));
    let mut app = App::default();
    app.session.cwd = Some(cwd);
    app.session.tokens = (0, 600_000);

    let row = bottom_bar_row(&mut app, WIDTH);

    let tail = " ⧉ ctx   0%";
    let kept = WIDTH as usize - 1 - tail.chars().count();
    assert_eq!(row, format!("…{}{tail}", "x".repeat(kept)));
}

#[test]
fn a_short_cwd_is_left_untouched() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);

    let row = bottom_bar_row(&mut app, WIDTH);

    assert!(row.starts_with("/tmp/project "));
    assert!(row.ends_with("ctx        0/600k"));
}
