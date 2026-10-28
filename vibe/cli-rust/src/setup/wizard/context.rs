//! Provider/config resolution for onboarding: seeded from the server's
//! resolved view (`setup/status`), never from raw config.toml — the server
//! owns the read side too.

use crate::setup::auth::rpc::{ProviderView, SetupStatus};

pub const DEFAULT_MISTRAL_API_ENV_KEY: &str = "MISTRAL_API_KEY";
const DEFAULT_MISTRAL_SERVER_URL: &str = "https://api.mistral.ai";
const DEFAULT_MISTRAL_BROWSER_AUTH_URL: &str = "https://console.mistral.ai";
const DEFAULT_MISTRAL_BROWSER_AUTH_API_URL: &str = "https://console.mistral.ai/api";
pub const DEFAULT_VIBE_BASE_URL: &str = "https://chat.mistral.ai";
pub const DEFAULT_CONSOLE_BASE_URL: &str = "https://console.mistral.ai";

/// The default Mistral provider view (`DEFAULT_PROVIDERS[0]`'s onboarding
/// surface): what `OnboardingContext::default` seeds and the wizard's
/// "Mistral AI" sign-in target resets to.
pub fn default_provider() -> ProviderView {
    ProviderView {
        name: "mistral".into(),
        api_base: format!("{DEFAULT_MISTRAL_SERVER_URL}/v1"),
        api_key_env_var: DEFAULT_MISTRAL_API_ENV_KEY.into(),
        browser_auth_base_url: Some(DEFAULT_MISTRAL_BROWSER_AUTH_URL.into()),
        browser_auth_api_base_url: Some(DEFAULT_MISTRAL_BROWSER_AUTH_API_URL.into()),
        browser_auth_allow_origin_rewrite: false,
    }
}

/// What the onboarding wizard needs from the current config.
#[derive(Debug, Clone, PartialEq)]
pub struct OnboardingContext {
    pub provider: ProviderView,
    pub vibe_base_url: String,
    pub console_base_url: String,
    pub theme: String,
    /// The server's own browser-sign-in answer (`SetupStatus`), not a
    /// client-side guess: it also weighs the provider's backend.
    pub supports_browser_sign_in: bool,
    /// The server-resolved TLS trust flag (ADR 0015).
    pub enable_system_trust_store: bool,
    /// The administrator-pinned console URL (`VIBE_CUSTOM_CONSOLE_URL`), when
    /// the server resolved one: sign-in locks to it and the domain cannot be
    /// modified.
    pub pinned_console_url: Option<String>,
    /// A plugin's `setupText.locales` map, when a resolved plugin customizes
    /// the welcome text; `welcome_text` resolves it for the user's locale.
    pub setup_text_locales: Option<std::collections::HashMap<String, String>>,
    /// The resolved welcome text: the plugin's locale entry, or the built-in
    /// default when no plugin customizes it.
    pub welcome_text: String,
}

impl Default for OnboardingContext {
    fn default() -> Self {
        Self {
            provider: default_provider(),
            vibe_base_url: DEFAULT_VIBE_BASE_URL.into(),
            console_base_url: DEFAULT_CONSOLE_BASE_URL.into(),
            theme: "auto".into(),
            supports_browser_sign_in: true,
            enable_system_trust_store: false,
            pinned_console_url: None,
            setup_text_locales: None,
            welcome_text: crate::setup::wizard::WELCOME_TEXT.to_owned(),
        }
    }
}

impl OnboardingContext {
    /// Seed from the server's resolved view (`setup/status`); the server
    /// already applied the config layering, so the only client-side
    /// override left is `VIBE_THEME`, applied over the injected schema too
    /// (env wins over config).
    pub fn from_status(status: &SetupStatus) -> Self {
        let theme = std::env::var("VIBE_THEME")
            .ok()
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| status.theme.clone());
        let setup_text_locales = status.setup_text_locales.clone();
        let welcome_text = setup_text_locales
            .as_ref()
            .and_then(resolve_setup_text)
            .unwrap_or_else(|| crate::setup::wizard::WELCOME_TEXT.to_owned());
        Self {
            provider: status.provider.clone(),
            vibe_base_url: status.vibe_base_url.clone(),
            console_base_url: status.console_base_url.clone(),
            theme,
            supports_browser_sign_in: status.supports_browser_sign_in,
            enable_system_trust_store: status.enable_system_trust_store,
            pinned_console_url: status.pinned_console_url.clone(),
            setup_text_locales,
            welcome_text,
        }
    }
}

/// Pick the welcome text for the user's locale out of a plugin's
/// `setupText.locales` map: `VIBE_LOCALE` then the `LC_ALL`/`LC_MESSAGES`/
/// `LANG` chain (normalized to BCP 47 style), exact tag then language-only,
/// then the mandatory `en` entry.
fn resolve_setup_text(locales: &std::collections::HashMap<String, String>) -> Option<String> {
    for name in ["VIBE_LOCALE", "LC_ALL", "LC_MESSAGES", "LANG"] {
        let Ok(raw) = std::env::var(name) else {
            continue;
        };
        let tag = raw
            .trim()
            .replace('_', "-")
            .split('.')
            .next()
            .unwrap_or("")
            .to_ascii_lowercase();
        if tag.is_empty() || tag == "c" || tag == "posix" {
            continue;
        }
        if let Some(text) = locales.get(&tag) {
            return Some(text.clone());
        }
        let language = tag.split('-').next().unwrap_or("");
        if let Some(text) = locales.get(language) {
            return Some(text.clone());
        }
    }
    locales.get("en").cloned()
}

/// Whether the wizard still holds the launch snapshot: provider, console,
/// and vibe URL all equal what the server resolved before the wizard opened,
/// so no on-prem tenant resolution could add information.
pub fn matches_launch_snapshot(context: &OnboardingContext, snapshot: &OnboardingContext) -> bool {
    context.provider == snapshot.provider
        && context.console_base_url == snapshot.console_base_url
        && context.vibe_base_url == snapshot.vibe_base_url
}

/// Validate a custom domain URL: reject a broken scheme separator, upgrade
/// scheme-less input to `https://`, then require an http/https URL with a
/// host (pydantic's `HttpUrl`).
pub fn is_valid_custom_domain(value: &str) -> bool {
    let origin = value.trim();
    if origin.is_empty() {
        return false;
    }
    // `https:/example.com` has no scheme separator but a stray `:/`: it can
    // never become a valid origin, before or after the https upgrade.
    if !origin.contains("://") && origin.contains(":/") {
        return false;
    }
    let normalized = if !origin.contains("://") {
        format!("https://{origin}")
    } else {
        origin.to_owned()
    };
    match url::Url::parse(&normalized) {
        Ok(url) => matches!(url.scheme(), "http" | "https") && url.host().is_some(),
        Err(_) => false,
    }
}

/// Resolve browser auth URLs from a domain plus its optional split-horizon
/// API base: the API base defaults to the browser base's own `/api`.
pub fn resolve_browser_auth_urls(domain: &str, api_base_url: Option<&str>) -> (String, String) {
    let base = normalize_origin(domain);
    let api = match api_base_url {
        Some(url) if !url.trim().is_empty() => normalize_origin(url),
        _ => format!("{base}/api"),
    };
    (base, api)
}

/// Whether the sign-in URLs cross origins.
pub fn browser_auth_requires_origin_rewrite(browser_base_url: &str, api_base_url: &str) -> bool {
    origin_key(browser_base_url) != origin_key(api_base_url)
}

/// The CLI-reachable base for account calls: the API base's origin in a
/// split-horizon setup, the browser base otherwise.
pub fn browser_auth_account_base(browser_base_url: &str, api_base_url: Option<&str>) -> String {
    let browser_base = normalize_origin(browser_base_url);
    let Some(api) = api_base_url else {
        return browser_base;
    };
    if !browser_auth_requires_origin_rewrite(browser_base_url, api) {
        return browser_base;
    }
    let Ok(parsed) = url::Url::parse(&normalize_origin(api)) else {
        return browser_base;
    };
    let host = parsed.host_str().unwrap_or_default();
    match parsed.port() {
        Some(port) => format!("{}://{host}:{port}", parsed.scheme()),
        None => format!("{}://{host}", parsed.scheme()),
    }
}

/// Heuristic: a Mistral-hosted private-cloud subdomain that is not the default
/// auth host.
pub fn is_likely_mistral_private_cloud_domain(domain: &str) -> bool {
    let Ok(parsed) = url::Url::parse(&normalize_origin(domain)) else {
        return false;
    };
    let Some(host) = parsed.host_str() else {
        return false;
    };
    host != "console.mistral.ai" && host.starts_with("console.") && host.ends_with(".mistral.ai")
}

/// The configured custom domain to seed the input with: a non-default
/// browser auth base. An absent URL is the wizard's "no browser sign-in"
/// state.
pub fn configured_custom_domain(provider: &ProviderView) -> Option<String> {
    let base = provider
        .browser_auth_base_url
        .as_deref()
        .unwrap_or_default();
    if base.is_empty() || base == DEFAULT_MISTRAL_BROWSER_AUTH_URL {
        return None;
    }
    Some(base.to_owned())
}

/// The configured split-horizon API base to seed the input with: only a
/// distinct cross-origin API base.
pub fn configured_custom_api_base(provider: &ProviderView) -> Option<String> {
    let base = provider
        .browser_auth_base_url
        .as_deref()
        .unwrap_or_default();
    let api = provider
        .browser_auth_api_base_url
        .as_deref()
        .unwrap_or_default();
    if base.is_empty() || api.is_empty() {
        return None;
    }
    if !browser_auth_requires_origin_rewrite(base, api) {
        return None;
    }
    Some(api.to_owned())
}

/// The configured split-horizon API base belonging to the admin-pinned
/// console: the pin fixes the browser sign-in host and must not clobber an
/// API base configured for the same deployment. Only returned when the
/// configured browser base normalizes to the pin.
pub fn pinned_console_api_base(provider: &ProviderView, pin: &str) -> Option<String> {
    let browser_base = provider.browser_auth_base_url.as_deref()?;
    let api_base = provider.browser_auth_api_base_url.as_deref()?;
    (normalize_origin(browser_base) == pin).then(|| api_base.to_owned())
}

/// (scheme, host, normalized port) — the origin identity two URLs share or not.
fn origin_key(value: &str) -> (String, String, Option<u16>) {
    match url::Url::parse(&normalize_origin(value)) {
        Ok(parsed) => {
            let port = parsed.port_or_known_default();
            (
                parsed.scheme().to_owned(),
                parsed.host_str().unwrap_or_default().to_owned(),
                port,
            )
        }
        Err(_) => (String::new(), String::new(), None),
    }
}

/// Normalize an origin: add https:// if missing, strip trailing /.
fn normalize_origin(value: &str) -> String {
    let origin = value.trim();
    let with_scheme = if !origin.contains("://") {
        format!("https://{origin}")
    } else {
        origin.to_owned()
    };
    with_scheme.trim_end_matches('/').to_owned()
}
