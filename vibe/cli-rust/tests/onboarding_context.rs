//! Custom-domain browser-auth URL resolution (Python `onboarding/context.py`).

use vibe_rs::setup::auth::rpc::ProviderView;
use vibe_rs::setup::wizard::context::{
    browser_auth_account_base, browser_auth_requires_origin_rewrite, configured_custom_api_base,
    configured_custom_domain, default_provider, is_likely_mistral_private_cloud_domain,
    is_valid_custom_domain, matches_launch_snapshot, resolve_browser_auth_urls, OnboardingContext,
};

#[test]
fn an_unchanged_context_matches_the_launch_snapshot() {
    let context = OnboardingContext::default();
    assert!(matches_launch_snapshot(&context, &context.clone()));
}

#[test]
fn a_custom_domain_or_url_change_leaves_the_launch_snapshot() {
    let snapshot = OnboardingContext::default();

    let mut context = snapshot.clone();
    context.provider.browser_auth_base_url = Some("https://console.example".into());
    assert!(!matches_launch_snapshot(&context, &snapshot));

    let mut context = snapshot.clone();
    context.console_base_url = "https://console.example".into();
    assert!(!matches_launch_snapshot(&context, &snapshot));

    let mut context = snapshot.clone();
    context.vibe_base_url = "https://vibe.example".into();
    assert!(!matches_launch_snapshot(&context, &snapshot));
}

#[test]
fn browser_auth_urls_default_to_the_browser_base_api() {
    let (base, api) = resolve_browser_auth_urls("connector.example", None);
    assert_eq!(base, "https://connector.example");
    assert_eq!(api, "https://connector.example/api");
}

#[test]
fn browser_auth_urls_honor_the_split_horizon_api_base() {
    let (base, api) = resolve_browser_auth_urls(
        "https://console.example",
        Some("https://connector.internal.example/api/"),
    );
    assert_eq!(base, "https://console.example");
    assert_eq!(api, "https://connector.internal.example/api");
}

#[test]
fn origin_rewrite_is_required_only_across_origins() {
    assert!(browser_auth_requires_origin_rewrite(
        "https://console.example",
        "https://connector.internal.example/api",
    ));
    assert!(!browser_auth_requires_origin_rewrite(
        "https://console.example",
        "https://console.example/api",
    ));
    // An explicit port equals its scheme's default port.
    assert!(!browser_auth_requires_origin_rewrite(
        "https://console.example:443",
        "https://console.example",
    ));
}

#[test]
fn account_base_follows_the_cli_reachable_origin() {
    // Single-host: the browser base keeps its path.
    assert_eq!(
        browser_auth_account_base("https://console.example", None),
        "https://console.example"
    );
    assert_eq!(
        browser_auth_account_base(
            "https://console.example",
            Some("https://console.example/api")
        ),
        "https://console.example"
    );
    // Split horizon: the API base's origin, port included.
    assert_eq!(
        browser_auth_account_base(
            "https://console.example",
            Some("https://connector.internal.example:8443/api")
        ),
        "https://connector.internal.example:8443"
    );
}

#[test]
fn private_cloud_heuristic_flags_console_subdomains() {
    assert!(is_likely_mistral_private_cloud_domain(
        "console.acme.mistral.ai"
    ));
    assert!(!is_likely_mistral_private_cloud_domain(
        "console.mistral.ai"
    ));
    assert!(!is_likely_mistral_private_cloud_domain("connector.example"));
}

fn provider(base: &str, api: &str) -> ProviderView {
    ProviderView {
        browser_auth_base_url: Some(base.into()),
        browser_auth_api_base_url: Some(api.into()),
        ..default_provider()
    }
}

#[test]
fn seeds_only_surface_meaningful_configured_values() {
    let default = default_provider();
    assert_eq!(configured_custom_domain(&default), None);
    assert_eq!(configured_custom_api_base(&default), None);

    let custom = provider("https://console.example", "https://console.example/api");
    assert_eq!(
        configured_custom_domain(&custom),
        Some("https://console.example".into())
    );
    // A same-origin API base is the derived default; it would clutter the field.
    assert_eq!(configured_custom_api_base(&custom), None);

    let split = provider(
        "https://console.example",
        "https://connector.internal.example/api",
    );
    assert_eq!(
        configured_custom_api_base(&split),
        Some("https://connector.internal.example/api".into())
    );
}

#[test]
fn custom_domain_validation_accepts_python_valid_urls() {
    for value in [
        "example.com",
        "https://example.com",
        "http://example.com",
        "sub.domain.example.com",
        "https://custom.example.com/api",
        "http://localhost:8080",
        "localhost",
        "my-company.internal",
        "192.168.1.10",
        "http://[::1]:8080",
    ] {
        assert!(is_valid_custom_domain(value), "{value}");
    }
}

#[test]
fn custom_domain_validation_rejects_python_invalid_urls() {
    for value in [
        "",
        "https://",
        "https:/",
        "http://",
        "https:// ",
        "example .com",
        "https://exa mple.com",
        "ftp://example.com",
        "http://example.com:notaport",
    ] {
        assert!(!is_valid_custom_domain(value), "{value}");
    }
}
