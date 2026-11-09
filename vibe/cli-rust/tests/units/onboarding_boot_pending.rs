//! The pre-boot welcome screen says the boot is converging, not "Press Enter".

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use vibe_rs::app::App;
use vibe_rs::setup::wizard::paint;
use vibe_rs::setup::wizard::{OnboardingState, Screen};

/// The wizard paints a full-width terminal; the welcome rows sit far apart,
/// so a wide, short area is enough to hold the box and its hint row.
fn render(wizard: &mut OnboardingState) -> String {
    let mut app = App::default();
    let mut terminal = Terminal::new(TestBackend::new(80, 12)).expect("terminal");
    terminal
        .draw(|frame| paint::draw(&mut app, wizard, frame, Rect::new(0, 0, 80, 12)))
        .expect("draw");
    terminal
        .backend()
        .buffer()
        .content()
        .iter()
        .map(|cell| cell.symbol())
        .collect()
}

fn welcome_state(boot_pending: bool) -> OnboardingState {
    OnboardingState {
        open: true,
        screen: Screen::Welcome,
        welcome_done: true,
        boot_pending,
        ..OnboardingState::default()
    }
}

#[test]
fn boot_pending_welcome_says_the_boot_is_converging() {
    let mut wizard = welcome_state(true);
    let screen = render(&mut wizard);
    assert!(screen.contains("Starting the app server"));
    assert!(!screen.contains("Enter continue"));
}

#[test]
fn booted_welcome_keeps_the_enter_hint() {
    let mut wizard = welcome_state(false);
    let screen = render(&mut wizard);
    assert!(screen.contains("Enter continue"));
    assert!(!screen.contains("Starting the app server"));
}
