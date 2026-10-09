//! The narrator status row reserves its content width in each state.

use vibe_rs::app::App;
use vibe_rs::turn_summary::NarratorState;
use vibe_rs::ui::narrator::width;

#[test]
fn row_width_follows_the_narrator_state() {
    let mut app = App::default();
    assert_eq!(width(&app), 0);
    app.narrator.state = NarratorState::Summarizing;
    assert_eq!(
        width(&app),
        "█ summarizing Esc/Ctrl+C to stop".chars().count() as u16
    );
    app.narrator.state = NarratorState::Speaking;
    for frame in 0..6 {
        app.narrator.frame = frame;
        assert_eq!(
            width(&app),
            "▂▅▇ speaking Esc/Ctrl+C to stop".chars().count() as u16
        );
    }
}
