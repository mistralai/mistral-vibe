//! Built-in web search and fetch result bodies (Python `WebSearchResultWidget`, `WebFetchResultWidget`).

use serde_json::Value;

use super::effect_output::{lines, BodyLine};
use crate::utils::clean::clean_output;
use crate::utils::text::single_line;

/// Python `_yield_text`: trim the outer newlines, sanitize, split.
pub(super) fn trimmed_text(output: &Value, key: &str) -> Vec<String> {
    output
        .get(key)
        .and_then(Value::as_str)
        .map(yield_text)
        .unwrap_or_default()
}

fn yield_text(text: &str) -> Vec<String> {
    lines(&clean_output(text.trim_matches('\n')))
}

/// Query, answer, then source bullets; web text is sanitized and only http(s) sources link.
pub(super) fn web_search_lines(output: &Value) -> Vec<BodyLine> {
    let mut lines = output
        .get("query")
        .and_then(Value::as_str)
        .map(|query| lines(&clean_output(&format!("query: {query}"))))
        .unwrap_or_default();
    if let Some(answer) = output
        .get("answer")
        .and_then(Value::as_str)
        .filter(|answer| !answer.is_empty())
    {
        lines.extend(yield_text(&format!("answer: {answer}")));
    }
    let sources = output
        .get("sources")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    if !sources.is_empty() && !lines.is_empty() {
        lines.push(String::new());
    }
    if sources.len() > 1 {
        lines.push("Sources:".to_string());
    }
    let mut lines: Vec<BodyLine> = lines.into_iter().map(BodyLine::from).collect();
    lines.extend(
        sources
            .iter()
            .filter_map(source_label_url)
            .map(|(label, url)| BodyLine::source(label, is_safe_url(url).then(|| url.to_owned()))),
    );
    lines
}

/// A source's title, else its URL, flattened to one sanitized row.
fn source_label_url(source: &Value) -> Option<(String, &str)> {
    let field = |key| {
        source
            .get(key)
            .and_then(Value::as_str)
            .map(|text| single_line(&clean_output(text)))
            .unwrap_or_default()
    };
    let url = source
        .get("url")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let label = Some(field("title"))
        .filter(|title| !title.is_empty())
        .unwrap_or_else(|| field("url"));
    (!label.is_empty()).then_some((label, url))
}

/// Python `links._is_safe_url`: only http(s) sources open.
fn is_safe_url(url: &str) -> bool {
    url.split_once(':').is_some_and(|(scheme, _)| {
        scheme.eq_ignore_ascii_case("http") || scheme.eq_ignore_ascii_case("https")
    })
}
