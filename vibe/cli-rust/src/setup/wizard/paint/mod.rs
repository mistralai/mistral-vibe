//! Full-screen ratatui rendering for the onboarding wizard. `mod.rs` owns the
//! screen dispatch and the background fill; each screen renders its own body.

mod api_key;
mod browser_sign_in;
mod custom_domain;
mod input;
mod options;
mod panel;
mod theme_selection;
mod welcome;

use std::borrow::Cow;

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::Frame;

use crate::app::App;

use super::{OnboardingState, Screen};

/// Lay out the onboarding wizard and draw the active screen.
pub fn draw(app: &mut App, wizard: &mut OnboardingState, f: &mut Frame, area: Rect) {
    // Paint the theme background on every cell first; widgets set only fg.
    f.buffer_mut()
        .set_style(area, Style::default().bg(crate::ui::theme::background()));
    // The wizard owns the screen: rebuild the shared link hitmap like the
    // transcript painter does.
    app.view.link_hitmap.clear();
    let chat_frame = app.view.banner.chat_frame();
    // Read the option-screen inputs before the screens take `app` mutably.
    let auth_selected = wizard.auth_method_selected;
    let target_selected = wizard.sign_in_target_selected;
    let provider_selected = wizard.provider_selected;
    let active_provider = wizard
        .provider_choice
        .as_ref()
        .map(|choice| choice.active.name.clone())
        .unwrap_or_default();
    let override_warning = wizard
        .override_confirm_armed
        .then(|| wizard.override_confirm_domain.clone());
    let shell_key_override = wizard.context.shell_key_override.clone();
    match wizard.screen {
        Screen::Welcome => welcome::draw(wizard, f, area),
        Screen::ThemeSelection => theme_selection::draw(app, wizard, f, area),
        Screen::ProviderChoice => options::draw(
            app,
            wizard,
            f,
            area,
            provider_selected,
            "What do you want to set up?",
            &format!("Your active model uses {active_provider}"),
            Cow::Owned(options::provider_opts(&active_provider)),
            &chat_frame,
            None,
            None,
        ),
        Screen::AuthMethod => options::draw(
            app,
            wizard,
            f,
            area,
            auth_selected,
            "Welcome to Mistral Vibe",
            "Choose your sign in method",
            Cow::Borrowed(&options::AUTH_OPTS),
            &chat_frame,
            None,
            shell_key_override.as_deref(),
        ),
        Screen::SignInTarget => options::draw(
            app,
            wizard,
            f,
            area,
            target_selected,
            "Launch browser",
            "Where do you sign in?",
            Cow::Borrowed(&options::TARGET_OPTS),
            &chat_frame,
            override_warning.as_deref(),
            shell_key_override.as_deref(),
        ),
        Screen::CustomDomain => custom_domain::draw(app, wizard, f, area, &chat_frame),
        Screen::BrowserSignIn => browser_sign_in::draw(app, wizard, f, area, &chat_frame),
        Screen::ApiKey => api_key::draw(app, wizard, f, area, &chat_frame),
    }
}
