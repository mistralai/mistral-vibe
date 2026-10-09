//! Regression: a long cwd clipped the bottom bar's token counter.

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use vibe_rs::app::App;
use vibe_rs::ui::bottom_bar;

const WIDTH: u16 = 120;

fn render_row(app: &mut App) -> String {
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, 1)).expect("terminal");
    terminal
        .draw(|frame| bottom_bar::draw(app, frame, Rect::new(0, 0, WIDTH, 1)))
        .expect("draw");
    let buffer = terminal.backend().buffer();
    (0..WIDTH).map(|x| buffer[(x, 0)].symbol()).collect()
}

#[test]
fn a_long_cwd_is_ellipsized_and_keeps_the_token_counter() {
    let cwd = format!("/tmp/{}", "x".repeat(100));
    let mut app = App::default();
    app.session.cwd = Some(cwd);
    app.session.tokens = (0, 600_000);

    let row = render_row(&mut app);

    let pid = format!(" [PID {}]", std::process::id());
    let counter = "0/600k tokens (0%)";
    let path_width = WIDTH as usize - pid.len() - 1 - counter.len();
    let head = format!("/tmp/{}", "x".repeat(path_width - "/tmp/…".chars().count()));
    assert_eq!(row, format!("{head}…{pid} {counter}"));
}

#[test]
fn a_short_cwd_is_left_untouched() {
    let mut app = App::default();
    app.session.cwd = Some("/tmp/project".into());
    app.session.tokens = (0, 600_000);

    let row = render_row(&mut app);

    let pid = format!(" [PID {}]", std::process::id());
    assert!(row.starts_with(&format!("/tmp/project{pid} ")));
    assert!(row.ends_with("0/600k tokens (0%)"));
}
