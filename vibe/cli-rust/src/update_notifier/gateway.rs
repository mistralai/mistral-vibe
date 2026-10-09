//! The PyPI update gateway (Python `adapters/pypi_update_gateway.py`).

use std::time::Duration;

use serde_json::Value;

use super::pep440::{parse_pep440_version, Pep440Version};
pub use crate::utils::tls::{
    apply_tls_trust, apply_tls_trust_for, configure_tls_trust, custom_root_certificates,
    tls_trust_store, TlsTrustStore,
};

const DEFAULT_BASE_URL: &str = "https://pypi.org";
const TIMEOUT_SECONDS: u64 = 5;
const ACCEPT_HEADER: &str = "application/vnd.pypi.simple.v1+json";

/// Python `Update`: the gateway's answer.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Update {
    pub latest_version: String,
}

/// Python `UpdateGatewayCause`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpdateGatewayCause {
    TooManyRequests,
    Forbidden,
    NotFound,
    RequestFailed,
    ErrorResponse,
    InvalidResponse,
    Unknown,
}

/// Python `DEFAULT_GATEWAY_MESSAGES`, keyed by cause.
pub fn default_gateway_message(cause: UpdateGatewayCause) -> &'static str {
    match cause {
        UpdateGatewayCause::TooManyRequests => "Rate limit exceeded while checking for updates.",
        UpdateGatewayCause::Forbidden => "Request was forbidden while checking for updates.",
        UpdateGatewayCause::NotFound => {
            "Unable to fetch the releases. Please check your permissions."
        }
        UpdateGatewayCause::RequestFailed => "Network error while checking for updates.",
        UpdateGatewayCause::ErrorResponse => {
            "Unexpected response received while checking for updates."
        }
        UpdateGatewayCause::InvalidResponse => {
            "Received an invalid response while checking for updates."
        }
        UpdateGatewayCause::Unknown => "Unable to determine whether an update is available.",
    }
}

/// Python `UpdateGatewayError`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UpdateGatewayError {
    pub cause: UpdateGatewayCause,
    pub user_message: Option<String>,
}

impl UpdateGatewayError {
    pub fn new(cause: UpdateGatewayCause) -> Self {
        Self {
            cause,
            user_message: None,
        }
    }
}

/// The package manager whose answer an update check (or a cache entry)
/// belongs to. Detection follows the install: a uv tool asks uv, a brew
/// install asks brew, and anything else polls PyPI like Python.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpdateSource {
    /// The PyPI Simple API gateway.
    Pypi,
    /// uv's own resolution for the installed tool.
    Uv,
    /// brew's own answer for the installed formula.
    Brew,
}

impl UpdateSource {
    /// The `[update_cache].source` value written alongside an answer.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pypi => "pypi",
            Self::Uv => "uv",
            Self::Brew => "brew",
        }
    }
}

/// Python `UpdateGateway`: fetch the latest available release. `source` is
/// the manager the implementing gateway asks, stamped on every cache write so
/// a later reader can reject an answer a different install's manager gave.
pub trait UpdateGateway {
    fn fetch_update(
        &self,
    ) -> impl std::future::Future<Output = Result<Option<Update>, UpdateGatewayError>>;

    /// The manager this gateway consults; the default matches Python, whose
    /// only gateway is the PyPI Simple API.
    fn source(&self) -> UpdateSource {
        UpdateSource::Pypi
    }
}

/// Python `build_ssl_context` (ADR 0015): the one client builder for every
/// Vibe-owned HTTPS request, under the given trust store and the caller's
/// timeout. Redirects stay off (Python's httpx default).
pub fn build_http_client_for(
    trust: TlsTrustStore,
    timeout: Duration,
) -> Result<reqwest::Client, reqwest::Error> {
    let builder = reqwest::Client::builder()
        .timeout(timeout)
        // Python's httpx client does not follow redirects.
        .redirect(reqwest::redirect::Policy::none());
    apply_tls_trust_for(builder, trust).build()
}

/// The gateway's HTTP client: the effective trust policy (ADR 0015) with the
/// gateway's own timeout.
pub fn build_http_client() -> Result<reqwest::Client, reqwest::Error> {
    build_http_client_for(tls_trust_store(), Duration::from_secs(TIMEOUT_SECONDS))
}

/// The wizard's Vibe-owned HTTP client (browser sign-in, whoami): the shared
/// ADR 0015 builder under the server-resolved trust flag from `setup/status`.
/// Never falls back to a bare library default: the trust roots are always the
/// policy's choice. A build failure (e.g. the system trust store with no CA
/// certificates loaded) is returned, not panicked on: callers treat it like
/// any request failure.
pub fn wizard_http_client(
    timeout: Duration,
    enable_system_trust_store: bool,
) -> Result<reqwest::Client, reqwest::Error> {
    let trust = if enable_system_trust_store {
        TlsTrustStore::System
    } else {
        TlsTrustStore::BundledWebPki
    };
    build_http_client_for(trust, timeout)
}

/// Python `PyPIUpdateGateway` over the PyPI Simple API.
pub struct PyPIUpdateGateway {
    project_name: String,
    base_url: String,
}

impl PyPIUpdateGateway {
    pub fn new(project_name: &str) -> Self {
        Self::with_base_url(project_name, DEFAULT_BASE_URL)
    }

    /// Python's `base_url` constructor parameter. Production checks always
    /// target PyPI; only tests point this elsewhere.
    pub fn with_base_url(project_name: &str, base_url: &str) -> Self {
        Self {
            project_name: project_name.to_owned(),
            base_url: base_url.trim_end_matches('/').to_owned(),
        }
    }

    /// The Simple API project URL this gateway polls.
    pub fn request_url(&self) -> String {
        format!("{}/simple/{}/", self.base_url, self.project_name)
    }
}

impl UpdateGateway for PyPIUpdateGateway {
    async fn fetch_update(&self) -> Result<Option<Update>, UpdateGatewayError> {
        let client = build_http_client()
            .map_err(|_| UpdateGatewayError::new(UpdateGatewayCause::RequestFailed))?;
        let response = match client
            .get(self.request_url())
            .header("Accept", ACCEPT_HEADER)
            .send()
            .await
        {
            Ok(response) => response,
            Err(_) => return Err(UpdateGatewayError::new(UpdateGatewayCause::RequestFailed)),
        };
        if let Some(cause) = status_cause(response.status().as_u16()) {
            return Err(UpdateGatewayError::new(cause));
        }
        if response.status().is_client_error() || response.status().is_server_error() {
            return Err(UpdateGatewayError::new(UpdateGatewayCause::ErrorResponse));
        }
        match response.json::<Value>().await {
            Ok(data) => Ok(parse_response(&data)),
            Err(_) => Err(UpdateGatewayError::new(UpdateGatewayCause::InvalidResponse)),
        }
    }
}

/// The production check. The manager is decided by the running install:
/// uv's own resolution — the user's index, pinned `exclude-newer`, per-package
/// rules — governs a uv tool, brew's formula metadata governs a brew
/// install, and the rest poll the PyPI Simple API like Python.
pub enum UpdateCheckGateway {
    UvOracle(super::uv_oracle::UvOracleGateway),
    Brew(super::brew_oracle::BrewOracleGateway),
    PyPI(PyPIUpdateGateway),
}

impl UpdateCheckGateway {
    /// The check the CLI runs, against the manager that owns this install.
    /// Async because the probes shell out to `uv tool dir` and `brew --cellar`.
    pub async fn for_project(project_name: &str) -> Self {
        match install_source().await {
            UpdateSource::Uv => Self::UvOracle(super::uv_oracle::UvOracleGateway),
            UpdateSource::Brew => Self::Brew(super::brew_oracle::BrewOracleGateway),
            UpdateSource::Pypi => Self::PyPI(PyPIUpdateGateway::new(project_name)),
        }
    }
}

/// The manager this process's install was made with, mirroring the gateway
/// choice: a uv tool receipt names uv, an executable under brew's prefix
/// names brew, and everything else reads as PyPI. Python's CLI always polls
/// PyPI, so its source is the constant `Pypi`.
pub async fn install_source() -> UpdateSource {
    if super::uv_oracle::is_uv_managed().await {
        UpdateSource::Uv
    } else if super::brew_oracle::is_brew_install().await {
        UpdateSource::Brew
    } else {
        UpdateSource::Pypi
    }
}

impl UpdateGateway for UpdateCheckGateway {
    async fn fetch_update(&self) -> Result<Option<Update>, UpdateGatewayError> {
        match self {
            Self::UvOracle(gateway) => gateway.fetch_update().await,
            Self::Brew(gateway) => gateway.fetch_update().await,
            Self::PyPI(gateway) => gateway.fetch_update().await,
        }
    }

    fn source(&self) -> UpdateSource {
        match self {
            Self::UvOracle(_) => UpdateSource::Uv,
            Self::Brew(_) => UpdateSource::Brew,
            Self::PyPI(_) => UpdateSource::Pypi,
        }
    }
}

/// Python `_STATUS_CAUSES`: 404/403/429 map to their own causes.
fn status_cause(status: u16) -> Option<UpdateGatewayCause> {
    match status {
        404 => Some(UpdateGatewayCause::NotFound),
        403 => Some(UpdateGatewayCause::Forbidden),
        429 => Some(UpdateGatewayCause::TooManyRequests),
        _ => None,
    }
}

/// Python `fetch_update`'s body: the highest listed version that also has a
/// non-yanked file. Unparsable versions and filenames are skipped.
pub fn parse_response(data: &Value) -> Option<Update> {
    let versions = data
        .get("versions")
        .and_then(Value::as_array)
        .map(|versions| {
            versions
                .iter()
                .filter_map(|raw| raw.as_str())
                .filter_map(parse_pep440_version)
                .collect::<Vec<Pep440Version>>()
        })
        .unwrap_or_default();
    // Python: `sorted(valid_versions, reverse=True)` — stable, highest first.
    let mut valid_versions = versions;
    valid_versions.sort_by(|left, right| right.cmp(left));

    let non_yanked = non_yanked_versions(data);
    for version in valid_versions {
        if non_yanked.contains(&version) {
            return Some(Update {
                latest_version: version.as_str(),
            });
        }
    }
    None
}

fn non_yanked_versions(data: &Value) -> Vec<Pep440Version> {
    let files = data
        .get("files")
        .and_then(Value::as_array)
        .map(|files| {
            files
                .iter()
                // Python skips non-dict entries and yanked files.
                .filter(|file| file.as_object().is_some())
                .filter(|file| file.get("yanked").and_then(Value::as_bool) != Some(true))
                .filter_map(|file| file.get("filename").and_then(Value::as_str))
                .filter_map(parse_filename_version)
                .collect::<Vec<Pep440Version>>()
        })
        .unwrap_or_default();
    files
}

/// Python `_parse_filename_version`: the wheel's second `-`-separated part,
/// else the sdist's last one, else unparsable. Full PEP 440, like Python's
/// `parse_wheel_filename` / `parse_sdist_filename`.
fn parse_filename_version(filename: &str) -> Option<Pep440Version> {
    if let Some(stem) = filename.strip_suffix(".whl") {
        let mut parts = stem.split('-');
        let _name = parts.next()?;
        let version = parts.next()?;
        return parse_pep440_version(version);
    }
    let stem = filename
        .strip_suffix(".tar.gz")
        .or_else(|| filename.strip_suffix(".zip"))?;
    let version = stem.rsplit('-').next()?;
    parse_pep440_version(version)
}
