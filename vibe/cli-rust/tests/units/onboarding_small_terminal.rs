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

#[test]
fn a_long_sign_in_error_keeps_the_hint_keys_on_screen() {
    let mut app = App::default();
    let mut wizard = vibe_rs::setup::wizard::OnboardingState {
        open: true,
        screen: Screen::BrowserSignIn,
        ..vibe_rs::setup::wizard::OnboardingState::default()
    };
    wizard.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Error;
    wizard.browser_sign_in.message = "Failed to start browser sign-in: error sending request \
         for url (https://console.example.com/vibe/sign-in): client error (Connect): tcp connect \
         error: connection refused (via proxy http://10.0.0.1:8080)"
        .into();
    let (width, height) = (80u16, 24u16);
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("terminal");
    terminal
        .draw(|frame| paint::draw(&mut app, &mut wizard, frame, Rect::new(0, 0, width, height)))
        .expect("draw");
    let screen: Vec<String> = (0..height)
        .map(|row| {
            (0..width)
                .map(|col| {
                    terminal
                        .backend()
                        .buffer()
                        .cell((col, row))
                        .map(|cell| cell.symbol().to_string())
                        .unwrap_or_default()
                })
                .collect()
        })
        .collect();
    let joined = screen.join("\n");
    assert!(
        joined.contains("retry") && joined.contains("enter API key manually"),
        "hints must stay visible:\n{joined}"
    );
}

#[test]
fn a_long_error_on_a_short_terminal_keeps_the_step_cards() {
    let mut app = App::default();
    let mut wizard = vibe_rs::setup::wizard::OnboardingState {
        open: true,
        screen: Screen::BrowserSignIn,
        ..vibe_rs::setup::wizard::OnboardingState::default()
    };
    wizard.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Error;
    wizard.browser_sign_in.message = "Failed to start browser sign-in: error sending request \\
         for url (https://console.example.com/vibe/sign-in): client error (Connect): tcp connect \\
         error: connection refused (via proxy http://10.0.0.1:8080)"
        .into();
    let (width, height) = (80u16, 20u16);
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("terminal");
    terminal
        .draw(|frame| paint::draw(&mut app, &mut wizard, frame, Rect::new(0, 0, width, height)))
        .expect("draw");
    let joined: String = (0..height)
        .map(|row| {
            (0..width)
                .map(|col| {
                    terminal
                        .backend()
                        .buffer()
                        .cell((col, row))
                        .map(|cell| cell.symbol().to_string())
                        .unwrap_or_default()
                })
                .collect::<String>()
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert!(
        joined.contains("Open browser"),
        "cards must stay visible:\n{joined}"
    );
    assert!(
        joined.contains("…"),
        "the clamped message ends with an ellipsis:\n{joined}"
    );
    assert!(joined.contains("retry"), "hints stay visible:\n{joined}");
}
