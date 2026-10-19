//! The startup loading row keeps telling the user the engine is coming up
//! while `--resume`'s picker is on screen.

use ratatui::backend::TestBackend;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::ui;

const WIDTH: u16 = 120;
const HEIGHT: u16 = 30;

fn screen(app: &mut App) -> String {
    let mut terminal = Terminal::new(TestBackend::new(WIDTH, HEIGHT)).expect("terminal");
    terminal.draw(|frame| ui::draw(app, frame)).expect("draw");
    let buffer = terminal.backend().buffer();
    (0..HEIGHT)
        .flat_map(|y| (0..WIDTH).map(move |x| (x, y)))
        .map(|(x, y)| buffer[(x, y)].symbol().to_string())
        .collect()
}

#[test]
fn the_initializing_row_shows_behind_the_resume_picker() {
    let mut app = App::default();
    app.resume_picker.open = true;
    assert!(
        screen(&mut app).contains("Initializing"),
        "the startup spinner is hidden behind the resume picker"
    );
}
