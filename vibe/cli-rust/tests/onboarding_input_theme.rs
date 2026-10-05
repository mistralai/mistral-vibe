//! The wizard renders on the theme background: Textual fills every screen cell
//! with `$background` and its `Input` widgets render on it, so `Clear`-ed
//! areas (input cards, the welcome box) must repaint the theme background
//! instead of leaving the terminal default.

use ratatui::backend::TestBackend;
use ratatui::style::Color;
use ratatui::Terminal;
use vibe_rs::app::App;
use vibe_rs::setup::wizard::{paint, OnboardingState, Screen};
use vibe_rs::ui::theme;

fn assert_no_terminal_default_background(screen: Screen) {
    let mut app = App::default();
    let mut wizard = OnboardingState {
        screen,
        ..OnboardingState::default()
    };
    let mut terminal = Terminal::new(TestBackend::new(120, 40)).unwrap();
    terminal
        .draw(|f| paint::draw(&mut app, &mut wizard, f, f.area()))
        .unwrap();
    for cell in terminal.backend().buffer().content() {
        assert_ne!(
            cell.bg,
            Color::Reset,
            "a wizard cell was left on the terminal-default background"
        );
    }
}

#[test]
fn welcome_and_input_screens_paint_the_theme_background() {
    theme::set_active("atom-one-dark");
    // The invariant only bites on truecolor themes; ANSI themes use Reset.
    assert_ne!(theme::background(), Color::Reset);
    for screen in [Screen::Welcome, Screen::CustomDomain, Screen::ApiKey] {
        assert_no_terminal_default_background(screen);
    }
}
