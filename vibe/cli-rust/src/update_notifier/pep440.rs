//! PEP 440 version parsing and ordering (Python `packaging.version.Version`).

use std::cmp::Ordering;

mod segments;

use segments::{parse_dev, parse_epoch, parse_local, parse_post, parse_pre, parse_release};

/// Pre-release label, in Python `_PRE_RANK` sort order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum PreLabel {
    Alpha,
    Beta,
    ReleaseCandidate,
}

impl PreLabel {
    fn as_str(self) -> &'static str {
        match self {
            Self::Alpha => "a",
            Self::Beta => "b",
            Self::ReleaseCandidate => "rc",
        }
    }
}

/// Pre-release rank, including the two pseudo-ranks Python `_cmpkey` uses.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
enum PreRank {
    DevOnly,
    Alpha,
    Beta,
    ReleaseCandidate,
    Stable,
}

impl PreRank {
    fn from(pre: Option<(PreLabel, u64)>, post: Option<u64>, dev: Option<u64>) -> Self {
        match (pre, post, dev) {
            (None, None, Some(_)) => Self::DevOnly,
            (Some((label, _)), _, _) => match label {
                PreLabel::Alpha => Self::Alpha,
                PreLabel::Beta => Self::Beta,
                PreLabel::ReleaseCandidate => Self::ReleaseCandidate,
            },
            (None, _, _) => Self::Stable,
        }
    }
}

/// One local segment; numbers sort above strings (PEP 440).
#[derive(Clone, Debug, Eq, PartialEq)]
enum LocalSegment {
    Num(u64),
    Str(String),
}

impl LocalSegment {
    /// Python `_parse_local_version`: digit-only parts become numbers.
    fn parse(raw: &str) -> Self {
        match raw.parse::<u64>() {
            Ok(number) => Self::Num(number),
            // Digits overflowing u64 keep their string form; Python's
            // unbounded ints rank them numerically, which we cannot.
            Err(_) => Self::Str(raw.to_ascii_lowercase()),
        }
    }
}

impl Ord for LocalSegment {
    fn cmp(&self, other: &Self) -> Ordering {
        match (self, other) {
            (Self::Num(left), Self::Num(right)) => left.cmp(right),
            (Self::Num(_), Self::Str(_)) => Ordering::Greater,
            (Self::Str(_), Self::Num(_)) => Ordering::Less,
            (Self::Str(left), Self::Str(right)) => left.cmp(right),
        }
    }
}

impl PartialOrd for LocalSegment {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl LocalSegment {
    fn as_str(&self) -> String {
        match self {
            Self::Num(number) => number.to_string(),
            Self::Str(raw) => raw.clone(),
        }
    }
}

/// A parsed PEP 440 version: epoch, release, pre/post/dev, local segments.
#[derive(Clone, Debug, Eq)]
pub struct Pep440Version {
    epoch: u64,
    release: Vec<u64>,
    pre: Option<(PreLabel, u64)>,
    post: Option<u64>,
    dev: Option<u64>,
    local: Vec<LocalSegment>,
}

/// Python `Version(raw)`: PEP 440 with normalization; unparsable is `None`.
pub fn parse_pep440_version(raw: &str) -> Option<Pep440Version> {
    let mut rest = raw.trim();
    rest = rest
        .strip_prefix('v')
        .or_else(|| rest.strip_prefix('V'))
        .unwrap_or(rest);
    let (epoch, rest) = parse_epoch(rest);
    let (release, rest) = parse_release(rest)?;
    let (pre, rest) = parse_pre(rest);
    let (post, rest) = parse_post(rest);
    let (dev, rest) = parse_dev(rest);
    let (local, rest) = match rest.strip_prefix('+') {
        None => (Vec::new(), rest),
        Some(body) => parse_local(body)?,
    };
    if !rest.is_empty() {
        return None;
    }
    Some(Pep440Version {
        epoch,
        release,
        pre,
        post,
        dev,
        local,
    })
}

impl Pep440Version {
    /// Python `str(Version)`: the normalized, round-trippable form.
    pub fn as_str(&self) -> String {
        let mut out = String::new();
        if self.epoch > 0 {
            out.push_str(&self.epoch.to_string());
            out.push('!');
        }
        out.push_str(
            &self
                .release
                .iter()
                .map(u64::to_string)
                .collect::<Vec<_>>()
                .join("."),
        );
        if let Some((label, number)) = &self.pre {
            out.push_str(label.as_str());
            out.push_str(&number.to_string());
        }
        if let Some(post) = &self.post {
            out.push_str(".post");
            out.push_str(&post.to_string());
        }
        if let Some(dev) = &self.dev {
            out.push_str(".dev");
            out.push_str(&dev.to_string());
        }
        if !self.local.is_empty() {
            out.push('+');
            out.push_str(
                &self
                    .local
                    .iter()
                    .map(LocalSegment::as_str)
                    .collect::<Vec<_>>()
                    .join("."),
            );
        }
        out
    }

    /// The release with trailing zeros stripped, so `1.0.0` == `1` (PEP 440).
    fn release_key(release: &[u64]) -> &[u64] {
        let mut end = release.len();
        while end > 0 && release[end - 1] == 0 {
            end -= 1;
        }
        &release[..end]
    }

    /// The suffix key encoding pre/post/dev ranks and numbers.
    fn suffix_key(&self) -> (PreRank, u64, u8, u64, u8, u64) {
        (
            PreRank::from(self.pre, self.post, self.dev),
            self.pre.map(|(_, number)| number).unwrap_or(0),
            u8::from(self.post.is_some()),
            self.post.unwrap_or(0),
            u8::from(self.dev.is_none()),
            self.dev.unwrap_or(0),
        )
    }
}

impl Ord for Pep440Version {
    fn cmp(&self, other: &Self) -> Ordering {
        self.epoch
            .cmp(&other.epoch)
            .then_with(|| Self::release_key(&self.release).cmp(Self::release_key(&other.release)))
            .then_with(|| self.suffix_key().cmp(&other.suffix_key()))
            .then_with(|| match (self.local.is_empty(), other.local.is_empty()) {
                (true, true) => Ordering::Equal,
                (true, false) => Ordering::Less,
                (false, true) => Ordering::Greater,
                (false, false) => self.local.cmp(&other.local),
            })
    }
}

impl PartialOrd for Pep440Version {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// Equality follows the ordering key, so `2.25.6+build` != `2.25.6`.
impl PartialEq for Pep440Version {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
