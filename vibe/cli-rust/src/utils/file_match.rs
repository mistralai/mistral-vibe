//! Textual-compatible ranking for file completion candidates.

use std::collections::BTreeMap;

use crate::utils::fuzzy;

const MAX_ENTRIES_TO_PROCESS: usize = 32_000;

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
    path_prefix: &'a str,
    immediate_only: bool,
}

impl<'a> SearchContext<'a> {
    fn new(query: &'a str) -> Self {
        let suffix = query.rsplit('/').next().unwrap_or("");
        if query.is_empty() {
            Self {
                suffix,
                search_pattern: "",
                path_prefix: "",
                immediate_only: true,
            }
        } else if query.ends_with('/') {
            Self {
                suffix,
                search_pattern: "",
                path_prefix: query,
                immediate_only: true,
            }
        } else {
            Self::fuzzy(query)
        }
    }

    fn fuzzy(query: &'a str) -> Self {
        Self {
            suffix: query.rsplit('/').next().unwrap_or(""),
            search_pattern: query,
            path_prefix: "",
            immediate_only: false,
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
    for (path, is_dir) in paths.iter().take(MAX_ENTRIES_TO_PROCESS) {
        if !matches_context(path, *is_dir, context) || !is_visible(path, context) {
            continue;
        }
        let fuzzy_score = if context.search_pattern.is_empty() {
            0
        } else if let Some(score) = fuzzy::score(context.search_pattern, path) {
            score
        } else {
            continue;
        };
        let label = mention(path, *is_dir);
        matches.push((label, match_rank(path, *is_dir, context, fuzzy_score)));
        if context.search_pattern.is_empty() && matches.len() >= limit {
            break;
        }
    }
    matches.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    matches.truncate(limit);
    matches
}

fn matches_context(path: &str, is_dir: bool, context: &SearchContext<'_>) -> bool {
    if !context.path_prefix.is_empty() {
        let prefix = context.path_prefix.trim_end_matches('/');
        return !(is_dir && path == prefix) && is_immediate_child(path, context.path_prefix);
    }
    !context.immediate_only || !path.contains('/')
}

fn is_immediate_child(path: &str, prefix: &str) -> bool {
    let prefix = format!("{}/", prefix.trim_end_matches('/'));
    let after = if let Some(after) = path.strip_prefix(&prefix) {
        after
    } else if let Some(index) = path.find(&prefix) {
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

fn match_rank(
    path: &str,
    is_dir: bool,
    context: &SearchContext<'_>,
    fuzzy_score: i64,
) -> MatchRank {
    let query = context.suffix.to_lowercase();
    let name = path.rsplit('/').next().unwrap_or(path).to_lowercase();
    let relative = path.to_lowercase();
    let (stem, extension) = split_filename(&name);
    let (query_stem, query_extension) = split_filename(&query);
    let looks_like_filename = query.contains('.');
    MatchRank {
        exact_directory: is_dir && relative == context.search_pattern.to_lowercase(),
        immediate_child_of_exact_path: context.search_pattern.contains('/')
            && is_immediate_child(&relative, &context.search_pattern.to_lowercase()),
        exact_filename: looks_like_filename && name == query,
        preferred_stem_match: stem == query && extension != ".lock",
        exact_stem: stem == query || (looks_like_filename && stem == query_stem),
        stem_prefix: stem.starts_with(if looks_like_filename {
            query_stem
        } else {
            &query
        }),
        name_prefix: name.starts_with(&query),
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
