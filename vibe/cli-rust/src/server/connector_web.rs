//! Unified `connector_web_search.*` calls, read as the built-in web search and fetch.

use serde_json::{json, Map, Value};

use crate::utils::clean::clean_output;
use crate::utils::text::{single_line, thousands};

const WEB_SEARCH: &str = "connector_web_search.web_search";
const OPEN_URL: &str = "connector_web_search.open_url";

/// A header in the built-in tool's wording: verb, message, suffix.
pub type Header = (&'static str, String, &'static str);

/// The call facts the header and body derive from.
pub struct Call<'a> {
    pub tool_name: Option<&'a str>,
    pub input: Option<&'a Value>,
    pub output: Option<&'a Value>,
    pub completed: bool,
    pub terminal: bool,
}

/// The built-in effect kind a connector web call renders as.
pub fn kind(tool_name: Option<&str>) -> Option<&'static str> {
    match tool_name? {
        WEB_SEARCH => Some("web_search"),
        OPEN_URL => Some("web_fetch"),
        _ => None,
    }
}

/// The built-in loading label (Python `get_status_text`).
pub fn status_text(tool_name: Option<&str>) -> Option<&'static str> {
    match tool_name? {
        WEB_SEARCH => Some("Searching the web"),
        OPEN_URL => Some("Fetching URL"),
        _ => None,
    }
}

/// The header: the result display once completed, else the call display.
pub fn header(call: &Call<'_>) -> Option<Header> {
    kind(call.tool_name)?;
    let result = call.output.filter(|_| call.completed);
    result
        .and_then(|output| result_header(call.tool_name?, call.input, output))
        .or_else(|| call_header(call.tool_name?, call.input, call.terminal))
}

/// The result reshaped into the built-in output, or `None` for an unknown shape.
pub fn output(tool_name: Option<&str>, input: Option<&Value>, output: &Value) -> Option<Value> {
    let structured = structured(output)?;
    match tool_name? {
        WEB_SEARCH => {
            let sources = search_sources(structured)?
                .into_iter()
                .map(|(title, url)| json!({"title": title, "url": url}))
                .collect::<Vec<_>>();
            let mut output = json!({"sources": sources});
            if let Some(query) = query(input) {
                output["query"] = query.into();
            }
            Some(output)
        }
        OPEN_URL => {
            let page = page(structured)?;
            Some(json!({"url": page.url, "content": page.content}))
        }
        _ => None,
    }
}

/// Python `get_call_display`: the quoted query, or the fetched domain.
fn call_header(tool_name: &str, input: Option<&Value>, settled: bool) -> Option<Header> {
    let (running, done, message) = match tool_name {
        WEB_SEARCH => (
            "Searching",
            "Searched",
            format!("the web: {}", quoted(query(input)?)),
        ),
        OPEN_URL => {
            let url = input?.get("url")?.as_str()?;
            ("Fetching", "Fetched", domain(url))
        }
        _ => return None,
    };
    Some((if settled { done } else { running }, message, ""))
}

/// Python `get_result_display`; an unreadable page shows no size, only a suffix.
fn result_header(tool_name: &str, input: Option<&Value>, output: &Value) -> Option<Header> {
    let structured = structured(output)?;
    match tool_name {
        WEB_SEARCH => {
            let count = search_sources(structured)?.len();
            let plural = if count == 1 { "" } else { "s" };
            let query = quoted(query(input)?);
            Some(("Searched", format!("{query} ({count} source{plural})"), ""))
        }
        OPEN_URL => {
            let page = page(structured)?;
            if !page.readable {
                return Some(("Fetched", page.url.to_owned(), "(unreadable)"));
            }
            let chars = thousands(page.content.chars().count() as u64);
            Some(("Fetched", format!("{} ({chars} chars)", page.url), ""))
        }
        _ => None,
    }
}

fn query(input: Option<&Value>) -> Option<&str> {
    input?
        .get("query")?
        .as_str()
        .filter(|query| !query.is_empty())
}

/// The MCP result envelope's `structured_content` object.
fn structured(output: &Value) -> Option<&Map<String, Value>> {
    output.get("structured_content")?.as_object()
}

/// `web_search` results keyed by opaque ids, as `(title, url)` in `rank` order.
fn search_sources(results: &Map<String, Value>) -> Option<Vec<(&str, &str)>> {
    let mut ranked = results
        .values()
        .enumerate()
        .map(|(index, result)| {
            let url = result.get("url")?.as_str()?;
            let title = result.get("title").and_then(Value::as_str).unwrap_or("");
            let rank = result
                .get("rank")
                .and_then(Value::as_u64)
                .unwrap_or(u64::MAX);
            Some(((rank, index), (title, url)))
        })
        .collect::<Option<Vec<_>>>()?;
    ranked.sort_by_key(|(order, _)| *order);
    Some(ranked.into_iter().map(|(_, source)| source).collect())
}

struct Page<'a> {
    url: &'a str,
    content: &'a str,
    readable: bool,
}

/// `open_url` page; with `can_open: false`, `content` says why it was not read.
fn page(page: &Map<String, Value>) -> Option<Page<'_>> {
    Some(Page {
        url: page.get("url")?.as_str()?,
        content: page.get("content").and_then(Value::as_str).unwrap_or(""),
        readable: page
            .get("can_open")
            .and_then(Value::as_bool)
            .unwrap_or(true),
    })
}

/// Python `urlparse(url).netloc or url[:50]`.
fn domain(url: &str) -> String {
    let host = url
        .split_once("://")
        .and_then(|(_, rest)| rest.split(['/', '?', '#']).next())
        .filter(|host| !host.is_empty());
    match host {
        Some(host) => host.to_owned(),
        None => url.chars().take(50).collect(),
    }
}

/// The query as one readable, quoted row: escapes stripped, whitespace collapsed, invisible characters dropped.
fn quoted(text: &str) -> String {
    let text: String = single_line(&clean_output(text))
        .chars()
        .filter(|&c| !c.is_control() && !is_invisible(c))
        .collect();
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    format!("{quote}{text}{quote}")
}

/// Zero-width, soft-hyphen, bidi, and BOM characters, which can hide or reorder text.
fn is_invisible(c: char) -> bool {
    matches!(
        c,
        '\u{ad}'
            | '\u{200b}'..='\u{200f}'
            | '\u{202a}'..='\u{202e}'
            | '\u{2060}'..='\u{2064}'
            | '\u{2066}'..='\u{206f}'
            | '\u{feff}'
    )
}
