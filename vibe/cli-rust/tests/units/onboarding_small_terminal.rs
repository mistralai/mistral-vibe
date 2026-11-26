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

const SCREENS: [Screen; 8] = [
    Screen::Welcome,
    Screen::ThemeSelection,
    Screen::ProviderChoice,
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

/// Render one screen into a test terminal and return the visible text.
fn render_screen(screen: Screen, width: u16, height: u16) -> String {
    render_wizard(&mut wizard_on(screen), width, height)
}

fn wizard_on(screen: Screen) -> vibe_rs::setup::wizard::OnboardingState {
    vibe_rs::setup::wizard::OnboardingState {
        open: true,
        screen,
        ..vibe_rs::setup::wizard::OnboardingState::default()
    }
}

fn render_wizard(
    wizard: &mut vibe_rs::setup::wizard::OnboardingState,
    width: u16,
    height: u16,
) -> String {
    let mut app = App::default();
    let mut terminal = Terminal::new(TestBackend::new(width, height)).expect("terminal");
    terminal
        .draw(|frame| paint::draw(&mut app, wizard, frame, Rect::new(0, 0, width, height)))
        .expect("draw");
    (0..height)
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
        .join("\n")
}

/// VIBE-5760: the sign-in options stay visible in a short terminal instead of
/// only appearing after enlarging it.
#[test]
fn option_cards_stay_visible_in_a_short_terminal() {
    for height in [11u16, 12, 14, 18] {
        let joined = render_screen(Screen::AuthMethod, 80, height);
        assert!(
            joined.contains("Launch browser") && joined.contains("Use an API key"),
            "options must be visible at height {height}:\n{joined}"
        );
    }
}

/// VIBE-5760: the theme screen (the wizard's second page) shows its list in a
/// short terminal instead of a blank screen.
#[test]
fn theme_screen_stays_visible_in_a_short_terminal() {
    for height in [11u16, 13] {
        let joined = render_screen(Screen::ThemeSelection, 80, height);
        assert!(
            joined.contains("Select your preferred theme") && joined.contains("auto"),
            "theme list must be visible at height {height}:\n{joined}"
        );
    }
}

/// VIBE-5760: below the smallest renderable panel, every screen shows the
/// enlarge notice rather than nothing at all.
#[test]
fn a_tiny_terminal_shows_the_enlarge_notice() {
    for screen in SCREENS {
        let joined = render_screen(screen, 40, 3);
        assert!(
            joined.contains("Enlarge terminal to continue"),
            "{screen:?} must show the notice at height 3:\n{joined}"
        );
    }
}

/// The notice wraps instead of being cut off in a narrow terminal.
#[test]
fn the_enlarge_notice_wraps_in_a_narrow_terminal() {
    let joined: String = render_screen(Screen::AuthMethod, 12, 6)
        .split_whitespace()
        .collect();
    assert!(joined.contains("Enlargeterminaltocontinue"), "{joined}");
}

/// No screen ever renders blank: it draws content or the notice.
#[test]
fn no_screen_renders_blank_in_a_small_terminal() {
    for height in 1..=MAX_HEIGHT {
        for screen in SCREENS {
            let mut wizard = wizard_on(screen);
            arm_worst_case_state(&mut wizard);
            let joined = render_wizard(&mut wizard, 80, height);
            assert!(
                !joined.trim().is_empty(),
                "{screen:?} renders blank at height {height}"
            );
        }
    }
}

/// The browser sign-in step cards show once they fit without the decoration.
#[test]
fn browser_sign_in_steps_show_in_a_short_terminal() {
    let joined = render_screen(Screen::BrowserSignIn, 80, 14);
    assert!(
        vibe_rs::setup::wizard::sign_in::STEP_TITLES
            .iter()
            .all(|title| joined.contains(title)),
        "step cards must be visible at height 14:\n{joined}"
    );
}

/// The secondary rows under the cards give way before the cards do.
#[test]
fn trailing_rows_never_hide_cards_that_fit() {
    let mut auth = wizard_on(Screen::AuthMethod);
    auth.context.shell_key_override = Some("MISTRAL_API_KEY".into());
    let joined = render_wizard(&mut auth, 80, 11);
    assert!(
        joined.contains("Launch browser") && joined.contains("Use an API key"),
        "{joined}"
    );
    let mut browser = wizard_on(Screen::BrowserSignIn);
    browser.browser_sign_in.sign_in_url = Some("https://console.example.com/sign-in".into());
    browser.browser_sign_in.show_url_help = true;
    let joined = render_wizard(&mut browser, 80, 12);
    assert!(
        vibe_rs::setup::wizard::sign_in::STEP_TITLES
            .iter()
            .all(|title| joined.contains(title)),
        "{joined}"
    );
}

/// A long validation error is clipped below the input card instead of
/// replacing the screen with the notice.
#[test]
fn a_long_api_key_error_keeps_the_input_card() {
    let mut wizard = wizard_on(Screen::ApiKey);
    wizard.context.shell_key_override = Some("MISTRAL_API_KEY".into());
    wizard.api_key_input.validation = vibe_rs::setup::wizard::ValidationState::Invalid(
        "The API key was rejected by the server. Check that you copied it entirely, that it \
         belongs to the selected provider, that it has not been revoked, and try again."
            .into(),
    );
    let joined = render_wizard(&mut wizard, 40, 13);
    assert!(
        joined.contains("Paste API key") && joined.contains("The API key was rejected"),
        "{joined}"
    );
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
    let roomy = render_wizard(&mut wizard, 80, 20);
    assert!(
        roomy.contains("10.0.0.1:8080") && roomy.contains("retry"),
        "the shed decoration leaves room for the whole message:\n{roomy}"
    );
    let joined = render_wizard(&mut wizard, 80, 14);
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
