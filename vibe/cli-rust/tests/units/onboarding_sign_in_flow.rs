//! Sign-in URL validation (Python
//! `setup/auth/http_browser_sign_in_gateway.py::_validate_url_against_base_url`).

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
    // A traversal that normalizes back under the base is allowed, matching
    // Python's post-normpath comparison.
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
