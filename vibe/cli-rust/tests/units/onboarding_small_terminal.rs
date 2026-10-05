//! Regression: the wizard renders in terminals too small for its panels —
//! ratatui panics on any draw past the buffer bottom (PR review: most
//! screens hit this between 5 and 20 rows, realistic in a split pane).

use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::Terminal;

use vibe_rs::app::App;
use vibe_rs::setup::wizard::paint;
use vibe_rs::setup::wizard::Screen;

/// Widths including the ultra-narrow underflow cases; every height from
/// 1 to 30 crosses each screen's measured panic threshold.
const WIDTHS: [u16; 4] = [3, 12, 40, 80];
const MAX_HEIGHT: u16 = 30;

const SCREENS: [Screen; 7] = [
    Screen::Welcome,
    Screen::ThemeSelection,
    Screen::AuthMethod,
    Screen::SignInTarget,
    Screen::CustomDomain,
    Screen::BrowserSignIn,
    Screen::ApiKey,
];

#[test]
fn every_screen_renders_without_panicking_in_small_terminals() {
    for width in WIDTHS {
        for height in 1..=MAX_HEIGHT {
            for screen in SCREENS {
                let mut app = App::default();
                let mut wizard = vibe_rs::setup::wizard::OnboardingState {
                    open: true,
                    screen,
                    ..vibe_rs::setup::wizard::OnboardingState::default()
                };
                arm_worst_case_state(&mut wizard);
                let mut terminal =
                    Terminal::new(TestBackend::new(width, height)).expect("terminal");
                terminal
                    .draw(|frame| {
                        paint::draw(&mut app, &mut wizard, frame, Rect::new(0, 0, width, height))
                    })
                    .expect("draw");
            }
        }
    }
}

/// The tallest state each screen can reach: the finished welcome hint, the
/// armed override warning, the revealed sign-in URL, and a validation line.
fn arm_worst_case_state(wizard: &mut vibe_rs::setup::wizard::OnboardingState) {
    wizard.welcome_done = true;
    wizard.override_confirm_armed = true;
    wizard.override_confirm_domain = "console.example.com".into();
    wizard.browser_sign_in.reveal_sign_in_url = true;
    wizard.browser_sign_in.sign_in_url =
        Some("https://console.example.com/sign-in?code=abcdefghijklmnop".into());
    wizard.browser_sign_in.show_url_help = true;
    wizard.custom_domain_feedback = 1;
}
