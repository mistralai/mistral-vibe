//! The Windows `ProxyServer`/`ProxyOverride` parser and the sign-in error source chain.

use vibe_rs::setup::auth::sign_in_gateway::request_error_details;
use vibe_rs::utils::system_proxy::{
    normalize_proxy_url, parse_proxy_override, parse_proxy_server, ProxySet,
};

#[test]
fn per_protocol_proxy_server_sets_each_scheme() {
    let set = parse_proxy_server("http=proxy.corp:8080;https=proxy.corp:8443");
    assert_eq!(set.http.as_deref(), Some("http://proxy.corp:8080"));
    assert_eq!(set.https.as_deref(), Some("http://proxy.corp:8443"));
}

#[test]
fn plain_proxy_server_applies_to_http_and_https() {
    let set = parse_proxy_server("proxy.corp:8080");
    assert_eq!(set.http.as_deref(), Some("http://proxy.corp:8080"));
    assert_eq!(set.https.as_deref(), Some("http://proxy.corp:8080"));
}

#[test]
fn bare_host_proxy_server_covers_both_schemes() {
    let set = parse_proxy_server("proxy");
    assert_eq!(set.http.as_deref(), Some("http://proxy"));
    assert_eq!(set.https.as_deref(), Some("http://proxy"));
}

#[test]
fn trailing_separator_and_unsupported_protocols_are_dropped() {
    let set = parse_proxy_server("http=proxy:8080;;ftp=proxy:8080;socks=proxy:1080;");
    assert_eq!(set.http.as_deref(), Some("http://proxy:8080"));
    assert_eq!(set.https, None);
}

#[test]
fn addresses_with_a_scheme_are_kept_verbatim() {
    let set = parse_proxy_server("https=https://proxy.corp:8443");
    assert_eq!(set.https.as_deref(), Some("https://proxy.corp:8443"));
}

#[test]
fn empty_proxy_server_is_an_empty_set() {
    assert_eq!(parse_proxy_server("  "), ProxySet::default());
}

#[test]
fn proxy_override_becomes_bypass_hosts() {
    assert_eq!(
        parse_proxy_override("<local>;*.corp.com; 10.0.0.0/8 ;"),
        vec![
            "corp.com".to_owned(),
            "10.0.0.0/8".to_owned(),
            "localhost".to_owned(),
            "127.0.0.1".to_owned(),
            "::1".to_owned(),
        ]
    );
    assert_eq!(
        parse_proxy_override("*.corp.com; 10.0.0.0/8"),
        vec!["corp.com".to_owned(), "10.0.0.0/8".to_owned()]
    );
}

#[test]
fn wildcard_bypass_entries_take_the_form_reqwest_matches() {
    // A leading `*.` is the GPO's subdomain wildcard: the bare domain matches
    // it and its subdomains in reqwest's suffix matcher.
    assert_eq!(
        parse_proxy_override("*.corp.com"),
        vec!["corp.com".to_owned()]
    );
    // The all-hosts wildcard passes through; IPv4 prefix wildcards become
    // their CIDR range, other wildcard forms cannot be expressed and drop.
    assert_eq!(parse_proxy_override("*"), vec!["*".to_owned()]);
    assert_eq!(parse_proxy_override("10.*"), vec!["10.0.0.0/8".to_owned()]);
    assert_eq!(
        parse_proxy_override("10.20.*"),
        vec!["10.20.0.0/16".to_owned()]
    );
    assert!(parse_proxy_override("10.*.5").is_empty());
    assert!(parse_proxy_override("10.999.*").is_empty());
    assert!(parse_proxy_override("foo*bar").is_empty());
}

#[test]
fn proxy_urls_are_normalized_like_the_python_client() {
    assert_eq!(
        normalize_proxy_url(" proxy:8080 "),
        Some("http://proxy:8080".to_owned())
    );
    assert_eq!(
        normalize_proxy_url("http://proxy:8080"),
        Some("http://proxy:8080".to_owned())
    );
    assert_eq!(normalize_proxy_url("  "), None);
}

#[tokio::test]
async fn request_error_details_names_the_deepest_cause() {
    let error = reqwest::Client::new()
        .get("http://")
        .send()
        .await
        .unwrap_err();
    let details = request_error_details(&error);
    assert!(
        details.contains("empty host"),
        "unexpected chain: {details}"
    );
}

use super::setup_probe::ENV_LOCK;
use vibe_rs::setup::auth::sign_in_gateway::SignInGateway;
use vibe_rs::utils::system_proxy::proxy_for_scheme;

#[test]
fn proxy_for_scheme_follows_the_env_precedence() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    std::env::remove_var("HTTP_PROXY");
    std::env::remove_var("http_proxy");
    std::env::remove_var("ALL_PROXY");
    std::env::remove_var("all_proxy");
    std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:10");
    assert_eq!(
        proxy_for_scheme("https").as_deref(),
        Some("http://127.0.0.1:10")
    );
    assert_eq!(proxy_for_scheme("http"), None);
    assert_eq!(
        proxy_for_scheme("HTTPS").as_deref(),
        Some("http://127.0.0.1:10")
    );
    std::env::remove_var("HTTPS_PROXY");
    std::env::remove_var("https_proxy");
    #[cfg(not(target_os = "windows"))]
    assert_eq!(proxy_for_scheme("https"), None);
}

#[test]
fn the_gateway_names_the_effective_proxy() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:10");
    let gateway = SignInGateway::new(
        "https://console.example.com",
        "https://api.example.com",
        false,
        false,
    )
    .unwrap();
    assert_eq!(gateway.proxy.as_deref(), Some("http://127.0.0.1:10"));
    std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:9");
    let gateway = SignInGateway::new(
        "https://console.example.com",
        "https://api.example.com",
        false,
        false,
    )
    .unwrap();
    assert_eq!(gateway.proxy.as_deref(), Some("http://127.0.0.1:9"));
    std::env::remove_var("HTTPS_PROXY");
    std::env::remove_var("https_proxy");
    let gateway = SignInGateway::new(
        "https://console.example.com",
        "https://api.example.com",
        false,
        false,
    )
    .unwrap();
    assert_eq!(gateway.proxy, None);
}

#[test]
fn proxy_credentials_are_redacted_in_diagnostics() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    std::env::set_var("HTTPS_PROXY", "http://user:pass@127.0.0.1:8080");
    let gateway = SignInGateway::new(
        "https://console.example.com",
        "https://api.example.com",
        false,
        false,
    )
    .unwrap();
    assert_eq!(gateway.proxy.as_deref(), Some("http://127.0.0.1:8080"));
    assert_eq!(
        vibe_rs::utils::system_proxy::redact_proxy_url("http://user@proxy:3128"),
        "http://proxy:3128"
    );
    assert_eq!(
        vibe_rs::utils::system_proxy::redact_proxy_url("http://proxy:3128"),
        "http://proxy:3128"
    );
    assert_eq!(
        vibe_rs::utils::system_proxy::redact_proxy_url("proxy:3128"),
        "proxy:3128"
    );
    std::env::remove_var("HTTPS_PROXY");
    std::env::remove_var("https_proxy");
}

use vibe_rs::utils::system_proxy::{proxy_action, ProxyAction};

#[test]
fn a_lone_no_proxy_wins_over_the_registry_like_getproxies() {
    let _guard = ENV_LOCK.lock().unwrap_or_else(|poison| poison.into_inner());
    for name in [
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ] {
        std::env::remove_var(name);
    }
    // Auto only where no registry proxy exists: Windows CI machines set their own.
    #[cfg(not(target_os = "windows"))]
    assert_eq!(proxy_action(), ProxyAction::Auto);
    // An empty NO_PROXY keeps the registry in charge, a lone one wins wholesale.
    std::env::set_var("NO_PROXY", "");
    assert_eq!(proxy_action(), ProxyAction::Auto);
    std::env::set_var("NO_PROXY", "*.corp.com,10.0.0.0/8");
    assert_eq!(proxy_action(), ProxyAction::Direct);
    assert_eq!(proxy_for_scheme("https"), None);
    std::env::set_var("HTTPS_PROXY", "http://127.0.0.1:8080");
    match proxy_action() {
        ProxyAction::Proxies(set) => {
            assert_eq!(set.https.as_deref(), Some("http://127.0.0.1:8080"));
            assert_eq!(
                set.no_proxy,
                vec!["*.corp.com".to_owned(), "10.0.0.0/8".to_owned()]
            );
        }
        other => panic!("expected explicit proxies, got {other:?}"),
    }
    std::env::remove_var("HTTPS_PROXY");
    std::env::remove_var("https_proxy");
    // Whitespace-only still counts as set, like CPython's `if value`.
    std::env::set_var("NO_PROXY", "  ");
    assert_eq!(proxy_action(), ProxyAction::Direct);
    std::env::remove_var("NO_PROXY");
    std::env::remove_var("no_proxy");
    std::env::remove_var("HTTPS_PROXY");
    std::env::remove_var("https_proxy");
    #[cfg(not(target_os = "windows"))]
    assert_eq!(proxy_action(), ProxyAction::Auto);
}
