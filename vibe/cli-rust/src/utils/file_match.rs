//! Ranking for file completion candidates.

use std::cmp::Ordering;
use std::collections::BTreeMap;

use nucleo_matcher::chars::{is_upper_case, normalize, to_lower_case};
use nucleo_matcher::{Config, Matcher, Utf32Str, Utf32String};

#[derive(Eq, Ord, PartialEq, PartialOrd)]
struct MatchRank {
    exact_directory: bool,
    immediate_child_of_exact_path: bool,
    exact_filename: bool,
    preferred_stem_match: bool,
    exact_stem: bool,
    stem_prefix: bool,
    name_prefix: bool,
    extension_match: bool,
    fuzzy_score: i64,
    shallow_path: i64,
}

struct SearchContext<'a> {
    suffix: &'a str,
    search_pattern: &'a str,
    /// The listed directory of a `dir/` query, with one trailing `/`.
    listed_dir: Option<String>,
    lower: LoweredQuery,
}

/// The query parts `match_rank` compares, lowercased once per search.
struct LoweredQuery {
    suffix: String,
    pattern: String,
    pattern_dir: Option<String>,
}

impl<'a> SearchContext<'a> {
    fn new(query: &'a str) -> Self {
        if query.is_empty() {
            Self::build(query, "", None)
        } else if query.ends_with('/') {
            Self::build(query, "", Some(query))
        } else {
            Self::fuzzy(query)
        }
    }

    fn fuzzy(query: &'a str) -> Self {
        Self::build(query, query, None)
    }

    fn build(query: &'a str, search_pattern: &'a str, listed_dir: Option<&str>) -> Self {
        let suffix = query.rsplit('/').next().unwrap_or("");
        let pattern = search_pattern.to_lowercase();
        Self {
            suffix,
            search_pattern,
            listed_dir: listed_dir.map(dir_prefix),
            lower: LoweredQuery {
                suffix: suffix.to_lowercase(),
                pattern_dir: pattern.contains('/').then(|| dir_prefix(&pattern)),
                pattern,
            },
        }
    }
}

pub fn matching(paths: &BTreeMap<String, bool>, query: &str, limit: usize) -> Vec<String> {
    let mut matches = ranked_matches(paths, &SearchContext::new(query), limit);
    if matches.is_empty() && query.ends_with('/') {
        let prefix = query.trim_end_matches('/');
        if !prefix.is_empty() && paths.get(prefix) != Some(&true) {
            matches = ranked_matches(paths, &SearchContext::fuzzy(query), limit);
        }
    }
    matches.into_iter().map(|(label, _)| label).collect()
}

fn ranked_matches(
    paths: &BTreeMap<String, bool>,
    context: &SearchContext<'_>,
    limit: usize,
) -> Vec<(String, MatchRank)> {
    let mut matches = Vec::new();
    let listing = context.search_pattern.is_empty();
    let mut scorer = (!listing).then(|| Scorer::new(context.search_pattern));
    for (path, is_dir) in paths {
        if !matches_context(path, *is_dir, context) || !is_visible(path, context) {
            continue;
        }
        let fuzzy_score = match &mut scorer {
            None => 0,
            Some(scorer) => {
                let Some(score) = scorer.score(path) else {
                    continue;
                };
                i64::from(score)
            }
        };
        let rank = match_rank(path, *is_dir, &context.lower, fuzzy_score);
        matches.push((path.as_str(), *is_dir, rank));
        if listing && matches.len() >= limit {
            break;
        }
        if matches.len() >= limit.max(1) * 2 {
            keep_best(&mut matches, limit);
        }
    }
    keep_best(&mut matches, limit);
    matches.sort_by(best_first);
    matches
        .into_iter()
        .map(|(path, is_dir, rank)| (mention(path, is_dir), rank))
        .collect()
}

/// Fuzzy scoring on every code point, the query and each path folded alike.
struct Scorer {
    matcher: Matcher,
    needle: Utf32String,
    haystack: Vec<char>,
    strip_accents: bool,
}

impl Scorer {
    fn new(pattern: &str) -> Self {
        // Folding is done here, alike on both sides; nucleo only ignores ASCII case.
        let mut config = Config::DEFAULT.match_paths();
        config.ignore_case = true;
        config.normalize = false;
        // An accented query asks for that accent; a plain one also finds accented paths.
        let strip_accents = pattern.chars().all(|c| {
            let lower = to_lower_case(c);
            normalize(lower) == lower
        });
        // nucleo expects a lowercase needle; paths keep ASCII case for its camelCase bonus.
        let folded: String = pattern
            .chars()
            .map(|c| fold(c.to_ascii_lowercase(), strip_accents))
            .collect();
        let needle = if folded.is_ascii() {
            Utf32String::Ascii(folded.into_boxed_str())
        } else {
            Utf32String::Unicode(folded.chars().collect())
        };
        Self {
            matcher: Matcher::new(config),
            needle,
            haystack: Vec::new(),
            strip_accents,
        }
    }

    fn score(&mut self, path: &str) -> Option<u16> {
        let haystack = if path.is_ascii() {
            Utf32Str::Ascii(path.as_bytes())
        } else {
            let strip = self.strip_accents;
            self.haystack.clear();
            self.haystack
                .extend(path.chars().map(|c| path_char(c, strip)));
            Utf32Str::Unicode(&self.haystack)
        };
        self.matcher.fuzzy_match(haystack, self.needle.slice(..))
    }
}

/// Lowercase non-ASCII letters, then strip accents when asked: nucleo does neither fully.
fn fold(c: char, strip_accents: bool) -> char {
    if c.is_ascii() {
        return c;
    }
    let lower = to_lower_case(c);
    if strip_accents {
        to_lower_case(normalize(lower))
    } else {
        lower
    }
}

/// A folded path letter that stays uppercase when nucleo folds it back, for its word-start bonus.
fn path_char(c: char, strip_accents: bool) -> char {
    let folded = fold(c, strip_accents);
    if c.is_ascii() || c.is_lowercase() || !is_upper_case(c) {
        return folded;
    }
    if folded.is_ascii() {
        folded.to_ascii_uppercase()
    } else if to_lower_case(c) == folded {
        c
    } else {
        folded
    }
}

/// Keep the `limit` best matches, unordered; bounds memory on very large indexes.
fn keep_best(matches: &mut Vec<(&str, bool, MatchRank)>, limit: usize) {
    if matches.len() > limit && limit > 0 {
        matches.select_nth_unstable_by(limit - 1, best_first);
    }
    matches.truncate(limit);
}

/// Best rank first; ties break on the mention label, without building it.
fn best_first(a: &(&str, bool, MatchRank), b: &(&str, bool, MatchRank)) -> Ordering {
    b.2.cmp(&a.2)
        .then_with(|| label_bytes(a).cmp(label_bytes(b)))
}

fn label_bytes<'a>(
    (path, is_dir, _): &(&'a str, bool, MatchRank),
) -> impl Iterator<Item = u8> + 'a {
    path.bytes().chain(is_dir.then_some(b'/'))
}

fn matches_context(path: &str, is_dir: bool, context: &SearchContext<'_>) -> bool {
    if let Some(dir) = &context.listed_dir {
        return !(is_dir && path == dir.trim_end_matches('/')) && is_immediate_child(path, dir);
    }
    !context.search_pattern.is_empty() || !path.contains('/')
}

fn dir_prefix(dir: &str) -> String {
    format!("{}/", dir.trim_end_matches('/'))
}

/// `prefix` ends with one `/`, as `dir_prefix` builds it.
fn is_immediate_child(path: &str, prefix: &str) -> bool {
    let after = if let Some(after) = path.strip_prefix(prefix) {
        after
    } else if let Some(index) = path.find(prefix) {
        if index > 0 && path.as_bytes()[index - 1] != b'/' {
            return false;
        }
        &path[index + prefix.len()..]
    } else {
        return false;
    };
    !after.is_empty() && !after.contains('/')
}

fn is_visible(path: &str, context: &SearchContext<'_>) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);
    !name.starts_with('.') || context.suffix.starts_with('.')
}

fn match_rank(path: &str, is_dir: bool, lower: &LoweredQuery, fuzzy_score: i64) -> MatchRank {
    let query = lower.suffix.as_str();
    let relative = path.to_lowercase();
    let name = relative.rsplit('/').next().unwrap_or(&relative);
    let (stem, extension) = split_filename(name);
    let (query_stem, query_extension) = split_filename(query);
    let looks_like_filename = query.contains('.');
    MatchRank {
        exact_directory: is_dir && relative == lower.pattern,
        immediate_child_of_exact_path: lower
            .pattern_dir
            .as_deref()
            .is_some_and(|dir| is_immediate_child(&relative, dir)),
        exact_filename: looks_like_filename && name == query,
        preferred_stem_match: stem == query && extension != ".lock",
        exact_stem: stem == query || (looks_like_filename && stem == query_stem),
        stem_prefix: stem.starts_with(if looks_like_filename {
            query_stem
        } else {
            query
        }),
        name_prefix: name.starts_with(query),
        extension_match: !query_extension.is_empty() && extension == query_extension,
        fuzzy_score,
        shallow_path: -(path.matches('/').count() as i64),
    }
}

fn split_filename(name: &str) -> (&str, &str) {
    let Some(dot) = name.rfind('.') else {
        return (name, "");
    };
    if dot == 0 || dot + 1 == name.len() {
        return (name, "");
    }
    (&name[..dot], &name[dot..])
}

fn mention(path: &str, is_dir: bool) -> String {
    let suffix = if is_dir { "/" } else { "" };
    format!("@{path}{suffix}")
}
