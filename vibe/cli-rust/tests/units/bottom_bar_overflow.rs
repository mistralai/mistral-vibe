//! Regression: a long cwd clipped the bottom bar's path instead of the counter.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use vibe_rs::app::App;
use vibe_rs::ui::bottom_bar;

const WIDTH: u16 = 120;

#[test]
fn a_long_cwd_keeps_the_path_and_clips_the_token_counter() {
    let cwd = format!("/tmp/{}", "x".repeat(100));
    let mut app = App::default();
    app.session.cwd = Some(cwd.clone());
    app.session.tokens = (0, 600_000);

    let mut terminal = Terminal::new(TestBackend::new(WIDTH, 1)).expect("terminal");
    terminal
        .draw(|frame| bottom_bar::draw(&mut app, frame, Rect::new(0, 0, WIDTH, 1)))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    let row: String = (0..WIDTH).map(|x| buffer[(x, 0)].symbol()).collect();

    // Python keeps the path and its PID, then a one-cell spacer, and lets the
    // screen edge cut the counter.
    let left = format!("{cwd} [PID {}]", std::process::id());
    let visible = WIDTH as usize - left.len() - 1;
    let counter = &"0/600k tokens (0%)"[..visible];
    assert_eq!(row.trim_end(), format!("{left} {counter}"));
}
