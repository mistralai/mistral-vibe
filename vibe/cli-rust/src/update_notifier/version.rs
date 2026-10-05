//! The update use-case's version parsing (Python `update.py` `_parse_version`).

use super::pep440::{parse_pep440_version, Pep440Version};

/// Python `_parse_version`: `Version(raw.replace("-", "+"))`. Hyphens become
/// local-version separators, so `1.6.1-jetbrains` parses — and, like every
/// local version, outranks the bare release. This is the use-case's surface
/// only; the gateway parses full PEP 440 directly (`pep440.rs`).
pub fn parse_version(raw: &str) -> Option<Pep440Version> {
    parse_pep440_version(&raw.replace('-', "+"))
}
