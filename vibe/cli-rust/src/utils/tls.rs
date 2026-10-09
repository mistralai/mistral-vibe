//! Outbound TLS trust policy for Vibe-owned HTTP clients (ADR 0015).

use std::sync::atomic::{AtomicBool, Ordering};

/// Python `configure_ssl_context`'s module global: the effective `enable_system_trust_store`.
static USE_SYSTEM_TRUST_STORE: AtomicBool = AtomicBool::new(false);

/// Python `configure_ssl_context`: set the trust policy for Vibe-owned HTTPS clients.
pub fn configure_tls_trust(enable_system_trust_store: bool) {
    USE_SYSTEM_TRUST_STORE.store(enable_system_trust_store, Ordering::Relaxed);
}

/// The two branches of Python `build_ssl_context`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TlsTrustStore {
    /// Python's `certifi` bundle: the compiled-in Mozilla root set.
    BundledWebPki,
    /// Python's `truststore.SSLContext`: the operating system trust store.
    System,
}

/// The effective trust store, consulted when a Vibe-owned client is built.
pub fn tls_trust_store() -> TlsTrustStore {
    if USE_SYSTEM_TRUST_STORE.load(Ordering::Relaxed) {
        TlsTrustStore::System
    } else {
        TlsTrustStore::BundledWebPki
    }
}

/// ADR 0015: pin the effective trust roots on any Vibe-owned HTTP client.
pub fn apply_tls_trust(builder: reqwest::ClientBuilder) -> reqwest::ClientBuilder {
    apply_tls_trust_for(builder, tls_trust_store())
}

/// ADR 0015: pin the given trust store's roots, plus `SSL_CERT_*` certificates.
pub fn apply_tls_trust_for(
    builder: reqwest::ClientBuilder,
    trust: TlsTrustStore,
) -> reqwest::ClientBuilder {
    let custom = custom_root_certificates();
    match trust {
        TlsTrustStore::System => builder.tls_certs_merge(custom),
        TlsTrustStore::BundledWebPki => {
            builder.tls_certs_only(bundled_root_certificates().chain(custom))
        }
    }
}

/// The compiled-in Mozilla root set, the analog of Python's certifi bundle.
fn bundled_root_certificates() -> impl Iterator<Item = reqwest::Certificate> {
    webpki_root_certs::TLS_SERVER_ROOT_CERTS.iter().map(|cert| {
        reqwest::Certificate::from_der(cert.as_ref()).expect("bundled roots are valid DER")
    })
}

/// `SSL_CERT_FILE`/`SSL_CERT_DIR` certificates, additive in both modes; bad sources are skipped.
pub fn custom_root_certificates() -> Vec<reqwest::Certificate> {
    let mut certificates = Vec::new();
    if let Some(path) = env_path("SSL_CERT_FILE") {
        load_pem_bundle(&path, &mut certificates);
    }
    if let Some(dir) = env_path("SSL_CERT_DIR") {
        match std::fs::read_dir(&dir) {
            Ok(entries) => {
                for entry in entries.flatten() {
                    // `metadata` follows symlinks, so c_rehash-style hashed links load too.
                    if std::fs::metadata(entry.path()).is_ok_and(|meta| meta.is_file()) {
                        load_pem_bundle(&entry.path(), &mut certificates);
                    }
                }
            }
            Err(err) => tracing::warn!(%err, ?dir, "Failed to read SSL_CERT_DIR"),
        }
    }
    certificates
}

fn env_path(name: &str) -> Option<std::path::PathBuf> {
    match std::env::var_os(name) {
        Some(value) if !value.is_empty() => Some(std::path::PathBuf::from(value)),
        _ => None,
    }
}

/// Append a file's PEM certificates; unreadable or unparsable files are skipped.
fn load_pem_bundle(path: &std::path::Path, out: &mut Vec<reqwest::Certificate>) {
    match std::fs::read(path) {
        Ok(bytes) => match reqwest::Certificate::from_pem_bundle(&bytes) {
            Ok(certificates) => out.extend(certificates),
            Err(err) => tracing::warn!(%err, ?path, "Failed to parse SSL certificates"),
        },
        Err(err) => tracing::warn!(%err, ?path, "Failed to read SSL certificates"),
    }
}
