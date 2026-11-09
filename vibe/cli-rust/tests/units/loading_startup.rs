//! The startup loading row keeps telling the user the engine is coming up
//! while `--resume`'s picker is on screen.

use vibe_rs::app::App;
use vibe_rs::ui;

use crate::support::{render, rows};

const WIDTH: u16 = 120;
const HEIGHT: u16 = 30;

fn screen(app: &mut App) -> String {
    rows(&render(WIDTH, HEIGHT, |frame| ui::draw(app, frame))).concat()
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
