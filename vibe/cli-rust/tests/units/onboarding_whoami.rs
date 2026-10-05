//! `sanitize_tenant_url` (Python `_sanitize_tenant_url`): strict https, a
//! host, no `..` in the raw path, trailing slashes stripped. `url::Url::parse`
//! normalizes `..` away, so the check runs on the raw string between host
//! and query/fragment — the Python `urlparse` parity that was the root cause
//! of the original bug (Item 1).

use vibe_rs::setup::auth::whoami::sanitize_tenant_url;

#[test]
fn a_plain_https_origin_is_accepted() {
    assert_eq!(
        sanitize_tenant_url("https://api.corp"),
        Some("https://api.corp".into())
    );
}

#[test]
fn a_trailing_slash_is_stripped() {
    assert_eq!(
        sanitize_tenant_url("https://api.corp/"),
        Some("https://api.corp".into())
    );
}

#[test]
fn a_path_is_accepted() {
    assert_eq!(
        sanitize_tenant_url("https://api.corp/v1/tenant"),
        Some("https://api.corp/v1/tenant".into())
    );
}

#[test]
fn a_path_with_dotdot_is_rejected() {
    assert!(sanitize_tenant_url("https://api.corp/path/../admin").is_none());
}

#[test]
fn a_dotdot_before_query_is_rejected() {
    assert!(sanitize_tenant_url("https://api.corp/../admin?x=1").is_none());
}

#[test]
fn a_dotdot_before_fragment_is_rejected() {
    assert!(sanitize_tenant_url("https://api.corp/..#frag").is_none());
}

#[test]
fn a_dotdot_in_query_only_is_accepted() {
    // `..` in the query string is not a path traversal.
    assert_eq!(
        sanitize_tenant_url("https://api.corp/path?x=.."),
        Some("https://api.corp/path?x=..".into())
    );
}

#[test]
fn http_is_rejected() {
    assert!(sanitize_tenant_url("http://api.corp").is_none());
}

#[test]
fn an_unparsable_value_is_rejected() {
    assert!(sanitize_tenant_url("not a url").is_none());
}

#[test]
fn a_hostless_https_is_rejected() {
    assert!(sanitize_tenant_url("https://").is_none());
}

#[test]
fn whitespace_is_trimmed() {
    assert_eq!(
        sanitize_tenant_url("  https://api.corp  "),
        Some("https://api.corp".into())
    );
}
