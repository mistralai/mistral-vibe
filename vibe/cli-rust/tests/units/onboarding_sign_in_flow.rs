//! Sign-in URL validation against a configured base.

use vibe_rs::setup::auth::sign_in_gateway::SignInErrorCode;
use vibe_rs::setup::auth::sign_in_url::validate_url;

fn start_failed() -> SignInErrorCode {
    SignInErrorCode::StartFailed
}

#[test]
fn same_origin_url_passes_through_unchanged() {
    let url = "https://console.example.com/auth/sign-in?process_id=abc";
    assert_eq!(
        validate_url(url, "https://console.example.com", false, start_failed()).unwrap(),
        url
    );
}

#[test]
fn default_port_matches_portless_base() {
    assert!(validate_url(
        "https://console.example.com:443/auth",
        "https://console.example.com",
        false,
        start_failed()
    )
    .is_ok());
}

#[test]
fn foreign_origin_is_rejected_without_rewrite() {
    assert!(validate_url(
        "https://evil.example.com/auth",
        "https://console.example.com",
        false,
        start_failed()
    )
    .is_err());
}

#[test]
fn rewrite_re_homes_the_url_onto_the_configured_origin() {
    // Split-horizon: the server returns its own public host; the CLI must
    // poll the configured virtual host instead.
    assert_eq!(
        validate_url(
            "https://connector.example.com/vibe/sign-in/poll?process_id=abc",
            "https://console.example.com",
            true,
            start_failed()
        )
        .unwrap(),
        "https://console.example.com/vibe/sign-in/poll?process_id=abc"
    );
}

#[test]
fn rewrite_drops_gateway_userinfo() {
    for url in [
        "https://stolen:creds@other.example/vibe/sign-in/poll?process_id=abc",
        "https://stolen@other.example/vibe/sign-in/poll?process_id=abc",
        "https://:creds@other.example/vibe/sign-in/poll?process_id=abc",
    ] {
        assert_eq!(
            validate_url(url, "https://console.example.com", true, start_failed()).unwrap(),
            "https://console.example.com/vibe/sign-in/poll?process_id=abc",
            "{url}"
        );
    }
}

#[test]
fn path_outside_the_base_path_is_rejected_even_with_rewrite() {
    assert!(validate_url(
        "https://connector.example.com/other/poll",
        "https://console.example.com/vibe",
        true,
        start_failed()
    )
    .is_err());
}

#[test]
fn dot_dot_traversal_is_rejected() {
    assert!(validate_url(
        "https://console.example.com/vibe/../../etc/poll",
        "https://console.example.com/vibe",
        false,
        start_failed()
    )
    .is_err());
    // A traversal that normalizes back under the base is allowed: the
    // comparison runs after normalization.
    assert!(validate_url(
        "https://console.example.com/vibe/sub/../poll",
        "https://console.example.com/vibe",
        false,
        start_failed()
    )
    .is_ok());
}

#[test]
fn encoded_traversal_is_rejected() {
    assert!(validate_url(
        "https://console.example.com/%2e%2e/etc",
        "https://console.example.com/vibe",
        false,
        start_failed()
    )
    .is_err());
}

#[test]
fn malformed_or_short_urls_error_instead_of_panicking() {
    for url in ["", "ab", "abc", "http:/x"] {
        assert!(
            validate_url(url, "https://console.example.com", false, start_failed()).is_err(),
            "must not panic on {url:?}"
        );
    }
}

use unicode_width::UnicodeWidthStr;
use vibe_rs::setup::wizard::sign_in::{
    step_detail_lines, steps_height, BrowserSignInState, SignInStep, SignInVariant,
    STEP_DONE_DETAILS, STEP_PENDING_DETAILS, WAITING_FOR_AUTHENTICATION,
};
const NO_CLAMP: usize = 50;

fn error_state(message: &str) -> BrowserSignInState {
    BrowserSignInState {
        step: SignInStep::Open,
        message: message.to_owned(),
        variant: SignInVariant::Error,
        running: false,
        sign_in_url: None,
        show_url_help: false,
        reveal_sign_in_url: false,
        sign_in_started_at: None,
    }
}

#[test]
fn short_messages_keep_the_three_four_row_cards() {
    let state = error_state("Failed to start browser sign-in.");
    assert_eq!(
        step_detail_lines(&state, 0, 0, 68, NO_CLAMP),
        vec![state.message.clone()]
    );
    assert_eq!(steps_height(&state, 0, 68, u16::MAX), 12);
}

#[test]
fn long_messages_wrap_inside_the_active_card() {
    let state = error_state(
        "Failed to start browser sign-in: error sending request for url (https://console.mistral.ai): client error (Connect): tcp connect error: connection refused",
    );
    let lines = step_detail_lines(&state, 0, 0, 68, NO_CLAMP);
    assert!(lines.len() > 1, "the message must wrap, got {lines:?}");
    // The card insets its text by 4 on each side: border plus padding.
    assert!(lines.iter().all(|line| line.width() <= 68 - 8));
    assert_eq!(lines.join(" "), state.message);
    // The active card grows by its wrapped lines, the idle cards stay four rows.
    assert_eq!(
        steps_height(&state, 0, 68, u16::MAX),
        lines.len() as u16 + 3 + 8
    );
}

#[test]
fn narrow_cards_still_wrap_to_at_least_one_column() {
    let state = error_state("Failed to start browser sign-in.");
    let lines = step_detail_lines(&state, 0, 0, 2, NO_CLAMP);
    assert!(lines.len() >= state.message.chars().count() / 2);
    assert!(lines.iter().all(|line| line.width() <= 1));
}

#[test]
fn done_and_idle_cards_use_their_fixed_one_liners() {
    let state = error_state("Failed to start browser sign-in.");
    assert_eq!(
        step_detail_lines(&state, 1, 0, 68, NO_CLAMP),
        vec![STEP_DONE_DETAILS[0].to_owned()]
    );
    assert_eq!(
        step_detail_lines(&state, 1, 2, 68, NO_CLAMP),
        vec![STEP_PENDING_DETAILS[2].to_owned()]
    );
}

#[test]
fn the_confirm_steps_gradient_stays_one_line() {
    let state = BrowserSignInState {
        step: SignInStep::Confirm,
        message: "Waiting for you to finish signing in...".to_owned(),
        variant: SignInVariant::Pending,
        ..error_state("")
    };
    assert_eq!(
        step_detail_lines(&state, 1, 1, 68, NO_CLAMP),
        vec![WAITING_FOR_AUTHENTICATION.to_owned()]
    );
    assert_eq!(steps_height(&state, 1, 68, u16::MAX), 12);
}

#[test]
fn the_steps_stack_clamps_to_its_row_budget() {
    let state = error_state(
        "Failed to start browser sign-in: error sending request for url (https://console.example.com/vibe/sign-in): client error (Connect): tcp connect error: connection refused (via proxy http://10.0.0.1:8080)",
    );
    // A 13-row budget leaves two wrapped lines; the second ends clamped.
    let lines = step_detail_lines(&state, 0, 0, 68, 2);
    assert_eq!(lines.len(), 2);
    assert!(lines[1].ends_with('…'));
    assert_eq!(steps_height(&state, 0, 68, 13), 13);
    // The minimum budget keeps one line and the stack still fits.
    let lines = step_detail_lines(&state, 0, 0, 68, 1);
    assert_eq!(lines.len(), 1);
    assert!(lines[0].ends_with('…'));
    assert_eq!(steps_height(&state, 0, 68, 11), 12);
}
