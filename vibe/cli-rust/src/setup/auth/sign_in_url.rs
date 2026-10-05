//! Sign-in URL validation against a configured base (Python
//! `_validate_url_against_base_url`).

use super::sign_in_gateway::{SignInError, SignInErrorCode};

/// Validate a server-returned URL against a configured base and return the
/// URL to actually use (Python `_validate_url_against_base_url`): reject
/// foreign origins unless rewrite is allowed, reject paths outside the
/// base path in either mode, and re-home a rewritten URL onto the
/// configured origin (scheme, userinfo, host, and port), keeping the
/// (path-validated) path and query.
pub fn validate_url(
    url: &str,
    base_url: &str,
    allow_origin_rewrite: bool,
    code: SignInErrorCode,
) -> Result<String, SignInError> {
    let current = match url::Url::parse(url) {
        Ok(parsed) => parsed,
        Err(_) => {
            return Err(SignInError {
                message: "URL validation failed.".into(),
                code,
            })
        }
    };
    let base = match url::Url::parse(base_url) {
        Ok(parsed) => parsed,
        Err(_) => {
            return Err(SignInError {
                message: "URL validation failed.".into(),
                code,
            })
        }
    };
    if !origins_match(&current, &base) && !allow_origin_rewrite {
        return Err(SignInError {
            message: "URL origin validation failed.".into(),
            code,
        });
    }
    if !path_is_under_base(current.path(), base.path()) {
        return Err(SignInError {
            message: "URL path validation failed.".into(),
            code,
        });
    }
    if origins_match(&current, &base) {
        return Ok(url.to_owned());
    }
    let mut rewritten = current;
    let _ = rewritten.set_scheme(base.scheme());
    let _ = rewritten.set_host(base.host_str());
    let _ = rewritten.set_port(base.port());
    // `set_host` leaves userinfo in place; Python replaces the whole
    // netloc, so gateway credentials must not survive onto the configured
    // origin. Clear the password first: an empty username keeps a
    // leftover `:pass@`.
    let _ = rewritten.set_password(base.password());
    let _ = rewritten.set_username(base.username());
    Ok(rewritten.to_string())
}

/// Canonical (scheme, host, effective-port) comparison; a URL's explicit
/// port equal to the scheme default matches a port-less base (Python
/// `normalize_url_origin`).
fn origins_match(a: &url::Url, b: &url::Url) -> bool {
    a.scheme() == b.scheme()
        && a.host_str() == b.host_str()
        && a.port_or_known_default() == b.port_or_known_default()
}

/// The URL's path must stay under the base path (Python
/// `_is_path_under_base_path`): percent-decode both, normalize, and accept
/// the base itself or anything below it. `..` segments that escape the base
/// are rejected.
fn path_is_under_base(path: &str, base_path: &str) -> bool {
    let normalized_base = normalize_url_path(base_path);
    let normalized_base = normalized_base.trim_end_matches('/');
    if normalized_base.is_empty() {
        return true;
    }
    let normalized_path = normalize_url_path(path);
    normalized_path == normalized_base
        || normalized_path.starts_with(&format!("{normalized_base}/"))
}

/// Percent-decode and `posixpath.normpath` a URL path.
fn normalize_url_path(path: &str) -> String {
    let decoded = percent_encoding::percent_decode_str(path)
        .decode_utf8_lossy()
        .to_string();
    normpath(&decoded)
}

/// `posixpath.normpath`: collapse `//`, drop `.` segments, resolve `..`
/// against the preceding segment, and drop a trailing slash (except at root).
fn normpath(path: &str) -> String {
    let absolute = path.starts_with('/');
    let mut segments: Vec<&str> = Vec::new();
    for segment in path.split('/') {
        match segment {
            "" | "." => continue,
            ".." => match segments.last() {
                Some(&"..") => segments.push(".."),
                Some(_) => {
                    segments.pop();
                }
                None if !absolute => segments.push(".."),
                None => {}
            },
            other => segments.push(other),
        }
    }
    let joined = segments.join("/");
    if absolute {
        format!("/{joined}")
    } else if joined.is_empty() {
        ".".into()
    } else {
        joined
    }
}
