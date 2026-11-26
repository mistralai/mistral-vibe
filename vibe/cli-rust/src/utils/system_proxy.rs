//! Vibe-owned HTTP proxies: `HTTP(S)_PROXY`/`ALL_PROXY`/`NO_PROXY` env vars plus
//! the Windows per-user `Internet Settings` GPO values, mirroring CPython's
//! `getproxies()` (env vars win over the registry wholesale). PAC/WPAD
//! auto-configuration (`AutoConfigURL`) is out of scope, as in CPython.

use std::env;

/// The proxy set a client builder applies: one proxy URL per request scheme plus bypass hosts.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct ProxySet {
    pub http: Option<String>,
    pub https: Option<String>,
    pub all: Option<String>,
    pub no_proxy: Vec<String>,
}

impl ProxySet {
    /// True when no proxy URL is configured (a bypass list alone proxies nothing).
    fn has_proxy(&self) -> bool {
        self.http.is_some() || self.https.is_some() || self.all.is_some()
    }
}

/// The effective proxies for a Vibe-owned client: env vars when any is set, else the registry.
pub fn effective_proxies() -> ProxySet {
    env_proxies().unwrap_or_else(registry_proxies)
}

/// `http://`-prefixed when the value carries no scheme, like the Python client's `_normalize_proxy_url`.
pub fn normalize_proxy_url(value: &str) -> Option<String> {
    let value = value.trim();
    if value.is_empty() {
        return None;
    }
    if value.contains("://") {
        Some(value.to_owned())
    } else {
        Some(format!("http://{value}"))
    }
}

/// `ProxyServer`/`HTTP(S)_PROXY` split into trimmed non-empty entries.
fn entries(value: &str, separator: char) -> impl Iterator<Item = &str> {
    value
        .split(separator)
        .map(str::trim)
        .filter(|entry| !entry.is_empty())
}

/// The Windows `ProxyServer` value: per-protocol `http=h:1;https=h:1` entries, or
/// one bare `host:port` for every protocol. Unknown protocols (ftp) and `socks=`
/// (reqwest here has no socks feature) are dropped; addresses carry `http://` by default.
pub fn parse_proxy_server(value: &str) -> ProxySet {
    let mut set = ProxySet::default();
    let value = value.trim();
    if value.is_empty() {
        return set;
    }
    if !value.contains('=') && !value.contains(';') {
        set.http = normalize_proxy_url(value);
        set.https = set.http.clone();
        return set;
    }
    for entry in entries(value, ';') {
        let Some((protocol, address)) = entry.split_once('=') else {
            continue;
        };
        let Some(address) = normalize_proxy_url(address) else {
            continue;
        };
        match protocol.trim().to_ascii_lowercase().as_str() {
            "http" => set.http = Some(address),
            "https" => set.https = Some(address),
            _ => {}
        }
    }
    set
}

/// The Windows `ProxyOverride` value as bypass hosts, entry by entry the
/// way reqwest's no-proxy matcher understands them: a leading `*.entry`
/// wildcard loses its prefix (the bare domain suffix-matches its
/// subdomains), an IPv4 prefix wildcard like `10.*` becomes its CIDR range,
/// `<local>` contributes loopback and localhost (its dot-less-host rule
/// cannot be expressed), and anything else the matcher cannot read is
/// dropped.
pub fn parse_proxy_override(value: &str) -> Vec<String> {
    let mut hosts: Vec<String> = entries(value, ';').filter_map(bypass_host).collect();
    if entries(value, ';').any(|entry| entry.eq_ignore_ascii_case("<local>")) {
        hosts.extend(["localhost", "127.0.0.1", "::1"].map(str::to_owned));
    }
    hosts
}

/// One bypass entry in the matcher's vocabulary, or `None` when no form it
/// reads preserves the entry's intent.
fn bypass_host(entry: &str) -> Option<String> {
    let entry = entry.strip_prefix("*.").unwrap_or(entry);
    if entry.eq_ignore_ascii_case("<local>") {
        return None;
    }
    if entry.contains('*') && entry != "*" {
        return ipv4_wildcard_to_cidr(entry);
    }
    Some(entry.to_owned())
}

/// `10.*` becomes `10.0.0.0/8` and `10.20.*` becomes `10.20.0.0/16`, the way
/// hyper-util's own Windows reader translates them; mid-octet or non-IPv4
/// wildcards have no equivalent (None).
fn ipv4_wildcard_to_cidr(entry: &str) -> Option<String> {
    let parts: Vec<&str> = entry.split('.').collect();
    let wildcard = parts.iter().position(|part| *part == "*")?;
    if wildcard == 0 || wildcard > 3 || parts[wildcard..].iter().any(|part| *part != "*") {
        return None;
    }
    let mut octets = [0u8; 4];
    for (index, part) in parts[..wildcard].iter().enumerate() {
        octets[index] = part.parse().ok()?;
    }
    Some(format!(
        "{}.{}.{}.{}/{}",
        octets[0],
        octets[1],
        octets[2],
        octets[3],
        wildcard * 8
    ))
}

/// The proxy env vars; `Some` once any is set, even a lone non-empty
/// `NO_PROXY`: like CPython's `getproxies()`, the env path wins over the
/// registry wholesale.
fn env_proxies() -> Option<ProxySet> {
    let set = ProxySet {
        http: env_url("HTTP_PROXY"),
        https: env_url("HTTPS_PROXY"),
        all: env_url("ALL_PROXY"),
        no_proxy: Vec::new(),
    };
    let raw = env::var("no_proxy")
        .or_else(|_| env::var("NO_PROXY"))
        .unwrap_or_default();
    // Any non-empty value counts, like CPython's `if value`; entries trim themselves.
    if !set.has_proxy() && raw.is_empty() {
        return None;
    }
    Some(ProxySet {
        no_proxy: entries(&raw, ',').map(str::to_owned).collect(),
        ..set
    })
}

/// What a client builder does with the effective proxies: stay on reqwest's
/// automatic matcher, go direct, or carry explicit proxies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ProxyAction {
    /// No env var and no registry proxy: reqwest's automatic env/system matcher stays on.
    Auto,
    /// A lone `NO_PROXY` wins over the registry wholesale: never proxied.
    Direct,
    /// Explicit proxies with their bypass list.
    Proxies(ProxySet),
}

/// The action a client builder takes for the effective proxies.
pub fn proxy_action() -> ProxyAction {
    if let Some(set) = env_proxies() {
        if !set.has_proxy() {
            return ProxyAction::Direct;
        }
        return ProxyAction::Proxies(set);
    }
    let set = registry_proxies();
    if !set.has_proxy() {
        return ProxyAction::Auto;
    }
    ProxyAction::Proxies(set)
}

/// The `NAME`/`name` env var as a proxy URL; lowercase wins, like CPython's two-pass scan.
fn env_url(name: &str) -> Option<String> {
    let value = env::var(name.to_ascii_lowercase())
        .or_else(|_| env::var(name))
        .ok()?;
    normalize_proxy_url(&value)
}

#[cfg(not(target_os = "windows"))]
fn registry_proxies() -> ProxySet {
    ProxySet::default()
}

/// Per-user settings only, like getproxies_registry(): a machine-policy
/// proxy (ProxySettingsPerUser=0) is out of scope.
#[cfg(target_os = "windows")]
fn registry_proxies() -> ProxySet {
    let Some(settings) = registry::InternetSettings::open() else {
        return ProxySet::default();
    };
    if !settings.bool_value("ProxyEnable") {
        return ProxySet::default();
    }
    let mut set = parse_proxy_server(&settings.string_value("ProxyServer").unwrap_or_default());
    set.no_proxy =
        parse_proxy_override(&settings.string_value("ProxyOverride").unwrap_or_default());
    set
}

/// Set the effective proxies; reqwest's automatic matcher stays on when
/// nothing is configured, and a lone `NO_PROXY` pins the client to direct.
pub fn apply(builder: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    match proxy_action() {
        ProxyAction::Auto => builder,
        ProxyAction::Direct => builder.no_proxy(),
        ProxyAction::Proxies(set) => {
            // The explicit set replaces the matcher: uncovered schemes go direct, like getproxies().
            let mut builder = builder.no_proxy();
            let no_proxy = reqwest::NoProxy::from_string(&set.no_proxy.join(","));
            if let Some(url) = &set.http {
                builder = apply_one(builder, url, reqwest::Proxy::http(url), &no_proxy);
            }
            if let Some(url) = &set.https {
                builder = apply_one(builder, url, reqwest::Proxy::https(url), &no_proxy);
            }
            if let Some(url) = &set.all {
                builder = apply_one(builder, url, reqwest::Proxy::all(url), &no_proxy);
            }
            builder
        }
    }
}

/// One proxy with the shared bypass list; unparsable URLs are logged and skipped.
fn apply_one(
    builder: reqwest::ClientBuilder,
    url: &str,
    proxy: Result<reqwest::Proxy, reqwest::Error>,
    no_proxy: &Option<reqwest::NoProxy>,
) -> reqwest::ClientBuilder {
    match proxy {
        Ok(proxy) => builder.proxy(proxy.no_proxy(no_proxy.clone())),
        Err(err) => {
            tracing::warn!(%err, url = %redact_proxy_url(url), "Ignoring unparsable proxy URL");
            builder
        }
    }
}

#[cfg(target_os = "windows")]
mod registry;

/// The effective proxy URL for requests with the given scheme, if one is
/// configured; diagnostics only, the bypass list is not consulted and the
/// userinfo is redacted, since these URLs reach on-screen errors and logs.
pub fn proxy_for_scheme(scheme: &str) -> Option<String> {
    let set = effective_proxies();
    match scheme.to_ascii_lowercase().as_str() {
        "http" => set.http.or(set.all),
        "https" => set.https.or(set.all),
        _ => set.all,
    }
    .map(|url| redact_proxy_url(&url))
}

/// The proxy URL without its userinfo: `http://user:pass@host` becomes
/// `http://host`, for every message and log line.
pub fn redact_proxy_url(url: &str) -> String {
    let Some((scheme, rest)) = url.split_once("://") else {
        return url.to_owned();
    };
    let Some((_, host)) = rest.rsplit_once('@') else {
        return url.to_owned();
    };
    format!("{scheme}://{host}")
}
