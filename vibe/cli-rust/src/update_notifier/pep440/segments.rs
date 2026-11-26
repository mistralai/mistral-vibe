//! The PEP 440 segment parsers: epoch, release, pre, post, dev, local.

use super::{LocalSegment, PreLabel};

/// The ASCII digit run at the head, and the rest after it.
fn split_digit_run(raw: &str) -> (&str, &str) {
    let end = raw
        .as_bytes()
        .iter()
        .position(|byte| !byte.is_ascii_digit())
        .unwrap_or(raw.len());
    raw.split_at(end)
}

/// A required digit run; digits overflowing u64 invalidate the version.
fn take_digit_run(raw: &str) -> Option<(u64, &str)> {
    let (digits, rest) = split_digit_run(raw);
    if digits.is_empty() {
        return None;
    }
    Some((digits.parse().ok()?, rest))
}

/// An optional digit run: absent is 0, overflow consumes nothing so the
/// leftover text invalidates the version.
fn take_optional_number(raw: &str) -> Option<(u64, &str)> {
    let (digits, rest) = split_digit_run(raw);
    if digits.is_empty() {
        return Some((0, rest));
    }
    Some((digits.parse().ok()?, rest))
}

fn strip_prefix_ci<'a>(raw: &'a str, prefix: &str) -> Option<&'a str> {
    let head = raw.get(..prefix.len())?;
    head.eq_ignore_ascii_case(prefix)
        .then_some(&raw[prefix.len()..])
}

fn strip_separator(raw: &str) -> (&str, bool) {
    match raw.as_bytes().first() {
        Some(b'.' | b'_' | b'-') => (&raw[1..], true),
        _ => (raw, false),
    }
}

pub(super) fn parse_epoch(raw: &str) -> (u64, &str) {
    if let Some((epoch, rest)) = take_digit_run(raw) {
        if let Some(rest) = rest.strip_prefix('!') {
            return (epoch, rest);
        }
    }
    (0, raw)
}

pub(super) fn parse_release(raw: &str) -> Option<(Vec<u64>, &str)> {
    let (first, mut rest) = take_digit_run(raw)?;
    let mut release = vec![first];
    while let Some(after_dot) = rest.strip_prefix('.') {
        let Some((number, after)) = take_digit_run(after_dot) else {
            break;
        };
        release.push(number);
        rest = after;
    }
    Some((release, rest))
}

pub(super) fn parse_pre(raw: &str) -> (Option<(PreLabel, u64)>, &str) {
    let (after_sep, _) = strip_separator(raw);
    let Some((label, after_label)) = match_pre_label(after_sep) else {
        return (None, raw);
    };
    let (after_sep, _) = strip_separator(after_label);
    let Some((number, after)) = take_optional_number(after_sep) else {
        return (None, raw);
    };
    (Some((label, number)), after)
}

/// Longest alias first, so `preview` wins over `pre` and `alpha` over `a`.
fn match_pre_label(raw: &str) -> Option<(PreLabel, &str)> {
    const ALIASES: [(PreLabel, &str); 8] = [
        (PreLabel::Alpha, "alpha"),
        (PreLabel::Alpha, "a"),
        (PreLabel::Beta, "beta"),
        (PreLabel::Beta, "b"),
        (PreLabel::ReleaseCandidate, "preview"),
        (PreLabel::ReleaseCandidate, "pre"),
        (PreLabel::ReleaseCandidate, "c"),
        (PreLabel::ReleaseCandidate, "rc"),
    ];
    for (label, alias) in ALIASES {
        let Some(rest) = strip_prefix_ci(raw, alias) else {
            continue;
        };
        if !rest.starts_with(|byte: char| byte.is_ascii_alphabetic()) {
            return Some((label, rest));
        }
    }
    None
}

pub(super) fn parse_post(raw: &str) -> (Option<u64>, &str) {
    // Implicit post: `-` directly followed by a number (PEP 440 `1.0-1`).
    if let Some(after_dash) = raw.strip_prefix('-') {
        if let Some((number, rest)) = take_digit_run(after_dash) {
            return (Some(number), rest);
        }
    }
    let (after_sep, _) = strip_separator(raw);
    let Some(after_label) = match_post_label(after_sep) else {
        return (None, raw);
    };
    let (after_sep, _) = strip_separator(after_label);
    let Some((number, rest)) = take_optional_number(after_sep) else {
        return (None, raw);
    };
    (Some(number), rest)
}

fn match_post_label(raw: &str) -> Option<&str> {
    for alias in ["post", "rev", "r"] {
        let Some(rest) = strip_prefix_ci(raw, alias) else {
            continue;
        };
        if !rest.starts_with(|byte: char| byte.is_ascii_alphabetic()) {
            return Some(rest);
        }
    }
    None
}

pub(super) fn parse_dev(raw: &str) -> (Option<u64>, &str) {
    let (after_sep, _) = strip_separator(raw);
    let Some(after_label) = strip_prefix_ci(after_sep, "dev") else {
        return (None, raw);
    };
    let (after_sep, _) = strip_separator(after_label);
    let Some((number, rest)) = take_optional_number(after_sep) else {
        return (None, raw);
    };
    (Some(number), rest)
}

/// The local segments after the `+`, split on `[._-]` (PEP 440).
pub(super) fn parse_local(body: &str) -> Option<(Vec<LocalSegment>, &str)> {
    let mut rest = body;
    let mut local = Vec::new();
    loop {
        let end = rest
            .as_bytes()
            .iter()
            .position(|byte| !byte.is_ascii_alphanumeric())
            .unwrap_or(rest.len());
        if end == 0 {
            return None;
        }
        let (segment, after) = rest.split_at(end);
        local.push(LocalSegment::parse(segment));
        let (after_sep, had_sep) = strip_separator(after);
        if !had_sep {
            return Some((local, after));
        }
        rest = after_sep;
    }
}
