//! The focused free-text row is a full-width cursor bar, like any focused option.

use ratatui::style::Color;
use vibe_rs::app::App;
use vibe_rs::question_app::other_option_idx;

use crate::support::{question, render, row, with_questions};

const WIDTH: u16 = 60;
const HEIGHT: u16 = 20;
/// The last content column, inside the border and the box padding.
const LAST_COLUMN: u16 = WIDTH - 3;

fn app_with_answer(answer: &str) -> App {
    let mut app = with_questions(vec![question("Choose", &["First"], true)]);
    app.question_app.other_texts.insert(0, answer.into());
    app.view.cursor_on = true;
    app
}

/// Background of the last content cell on the row showing `needle`.
fn row_end_bg(app: &mut App, needle: &str) -> Color {
    let buffer = render(WIDTH, HEIGHT, |f| {
        vibe_rs::ui::question_app::draw(app, f, f.area())
    });
    let y = (0..HEIGHT)
        .find(|&y| row(&buffer, y).contains(needle))
        .unwrap_or_else(|| panic!("no row shows {needle:?}"));
    buffer[(LAST_COLUMN, y)].bg
}

#[test]
fn a_focused_free_text_row_paints_the_bar_across_the_row() {
    for (answer, shown) in [("draft", "draft"), ("", "ype your answer")] {
        let mut app = app_with_answer(answer);
        let bar = row_end_bg(&mut app, "First");
        let other_idx = other_option_idx(&app).unwrap();
        app.question_app.list.select(other_idx);

        assert_ne!(row_end_bg(&mut app, "First"), bar);
        assert_eq!(row_end_bg(&mut app, shown), bar, "answer {answer:?}");
    }
}
