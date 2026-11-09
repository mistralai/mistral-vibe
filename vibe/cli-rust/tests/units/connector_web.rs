//! Unified web connector calls render as the built-in web search and fetch.

use serde_json::{json, Value};
use vibe_rs::server::{EffectEntry, HistoryEntry};
use vibe_rs::transcript::Transcript;

fn effect(tool_name: &str, input: Value, state: Value) -> EffectEntry {
    serde_json::from_value(json!({
        "detail": {
            "kind": "tool",
            "toolName": tool_name,
            "input": input,
            "display": {"verb": "Searching", "message": "the web",
                "settledVerb": "Searched", "settledMessage": "the web"},
        },
        "state": state,
    }))
    .unwrap()
}

fn completed(structured: Value) -> Value {
    json!({
        "status": "completed",
        "display": {"success": true, "verb": "Searched", "message": "the web"},
        "output": {
            "type": "success",
            "content": [{"type": "text", "text": structured.to_string()}],
            "structured_content": structured,
            "_meta": {"approval": {"decision": "execute"}},
        },
    })
}

fn summary(effect: &EffectEntry) -> (String, String, String) {
    let (verb, message, suffix) = effect.summary();
    (verb.to_owned(), message.to_owned(), suffix.to_owned())
}

fn body(effect: &EffectEntry) -> Vec<(String, String, Option<String>)> {
    effect
        .body()
        .into_iter()
        .map(|line| (line.lead.to_owned(), line.text, line.link))
        .collect()
}

fn header(verb: &str, message: &str, suffix: &str) -> (String, String, String) {
    (verb.into(), message.into(), suffix.into())
}

fn row(text: &str) -> (String, String, Option<String>) {
    (String::new(), text.into(), None)
}

#[test]
fn web_search_results_render_as_ranked_sources() {
    let search = effect(
        "connector_web_search.web_search",
        json!({"query": "peluche chat", "limit": 10}),
        completed(json!({
            "xNyQ0Yof": {"url": "https://b.example/cat", "title": "Second", "rank": 1,
                "description": "<strong>ignored</strong>", "snippets": ["ignored"],
                "can_open": true, "metadata": {"source": "brave"}},
            "CyGcVA9F": {"url": "https://a.example/cat", "title": "First", "rank": 0},
        })),
    );
    assert_eq!(search.kind(), Some("web_search"));
    assert_eq!(
        summary(&search),
        header("Searched", "'peluche chat' (2 sources)", "")
    );
    let source = |title: &str, url: &str| ("  • ".into(), title.into(), Some(url.into()));
    assert_eq!(
        body(&search),
        [
            row("query: peluche chat"),
            row(""),
            row("Sources:"),
            source("First", "https://a.example/cat"),
            source("Second", "https://b.example/cat"),
        ]
    );
}

#[test]
fn web_search_call_header_quotes_the_query_without_escaping() {
    let running = effect(
        "connector_web_search.web_search",
        json!({"query": "l'arbre"}),
        json!({"status": "running"}),
    );
    assert_eq!(
        summary(&running),
        header("Searching", "the web: \"l'arbre\"", "")
    );
    let empty = effect(
        "connector_web_search.web_search",
        json!({"query": "zidane"}),
        completed(json!({})),
    );
    assert_eq!(
        summary(&empty),
        header("Searched", "'zidane' (0 sources)", "")
    );
}

#[test]
fn open_url_renders_as_a_fetched_page() {
    let fetch = effect(
        "connector_web_search.open_url",
        json!({"url": "https://www.google.com/search?q=x"}),
        json!({"status": "running"}),
    );
    assert_eq!(fetch.kind(), Some("web_fetch"));
    assert_eq!(summary(&fetch), header("Fetching", "www.google.com", ""));

    let content = format!("\n# Title\n{}\n", "x".repeat(1200));
    let fetched = effect(
        "connector_web_search.open_url",
        json!({"url": "https://example.com/a"}),
        completed(json!({"url": "https://example.com/a", "content": content,
            "can_open": true, "title": null, "description": null, "date": null})),
    );
    assert_eq!(
        summary(&fetched),
        header("Fetched", "https://example.com/a (1,210 chars)", "")
    );
    assert_eq!(body(&fetched), [row("# Title"), row(&"x".repeat(1200))]);
}

#[test]
fn unreadable_page_says_so_in_the_suffix() {
    let blocked = effect(
        "connector_web_search.open_url",
        json!({"url": "https://google.com"}),
        completed(json!({"url": "https://google.com", "can_open": false,
            "content": "This page could not be read.", "title": null})),
    );
    assert_eq!(
        summary(&blocked),
        header("Fetched", "https://google.com", "(unreadable)")
    );
    assert_eq!(body(&blocked), [row("This page could not be read.")]);
}

#[test]
fn failed_open_url_keeps_the_url_header_and_error_body() {
    let failed = effect(
        "connector_web_search.open_url",
        json!({"url": "https://pypi.org/pypi/x/json"}),
        json!({"status": "failed", "display": {"success": false, "message": "blocked"},
            "error": {"code": "tool_denied", "message": "Smart approve blocked this action."}}),
    );
    assert_eq!(summary(&failed), header("Fetched", "pypi.org", ""));
    assert_eq!(
        body(&failed),
        [row("Error: Smart approve blocked this action.")]
    );
}

#[test]
fn unknown_result_shape_keeps_the_generic_body() {
    // A Runtime that changes the result shape must not lose the payload.
    let odd = effect(
        "connector_web_search.web_search",
        json!({"query": "zidane"}),
        completed(json!({"results": "not a result object"})),
    );
    assert_eq!(summary(&odd), header("Searched", "the web: 'zidane'", ""));
    assert!(body(&odd)
        .iter()
        .any(|(_, text, _)| text.contains("not a result object")));
}

#[test]
fn other_connector_tools_are_untouched() {
    let news = effect(
        "connector_web_search.news_search",
        json!({"query": "zidane"}),
        completed(json!({"a": {"url": "https://n.example", "title": "News"}})),
    );
    assert_eq!(news.kind(), Some("tool"));
    assert_eq!(summary(&news), header("Searched", "the web", ""));
}

#[test]
fn loading_label_uses_the_built_in_status_text() {
    let search = effect(
        "connector_web_search.web_search",
        json!({"query": "zidane"}),
        json!({"status": "running"}),
    );
    assert_eq!(search.status_text(), "Searching the web");
    let fetch = effect(
        "connector_web_search.open_url",
        json!({"url": "https://example.com"}),
        json!({"status": "running"}),
    );
    assert_eq!(fetch.status_text(), "Fetching URL");
}

#[test]
fn call_header_cleans_the_query_and_truncates_schemeless_urls() {
    let search = effect(
        "connector_web_search.web_search",
        json!({"query": "\x1b[31mzidane"}),
        json!({"status": "running"}),
    );
    assert_eq!(
        summary(&search),
        header("Searching", "the web: 'zidane'", "")
    );
    let hidden = effect(
        "connector_web_search.web_search",
        json!({"query": "zi\u{200b}dane\u{202e}"}),
        json!({"status": "running"}),
    );
    assert_eq!(
        summary(&hidden),
        header("Searching", "the web: 'zidane'", "")
    );
    let spaces = effect(
        "connector_web_search.web_search",
        json!({"query": "a\u{a0}b\u{ad}c\n\u{2003}d\u{3000}é"}),
        json!({"status": "running"}),
    );
    assert_eq!(
        summary(&spaces),
        header("Searching", "the web: 'a bc d é'", "")
    );
    let bare = effect(
        "connector_web_search.open_url",
        json!({"url": "x".repeat(80)}),
        json!({"status": "running"}),
    );
    assert_eq!(summary(&bare), header("Fetching", &"x".repeat(50), ""));
}

#[test]
fn search_without_a_query_shows_only_its_sources() {
    let search = effect(
        "connector_web_search.web_search",
        json!({}),
        completed(json!({"a": {"url": "https://a.example", "title": "A"}})),
    );
    assert_eq!(
        body(&search),
        [("  • ".into(), "A".into(), Some("https://a.example".into()))]
    );
}

#[test]
fn header_follows_the_call_from_running_to_completed() {
    let mut transcript = Transcript::default();
    transcript.add(&json!({"entry": {"id": "w1", "type": "effect",
        "detail": {"kind": "tool", "toolName": "connector_web_search.web_search",
            "input": {"query": "zidane"}},
        "state": {"status": "running"}}, "eventId": 1}));
    let header_of = |transcript: &Transcript| {
        let HistoryEntry::Effect(effect) = transcript.entry(0).unwrap().entry else {
            panic!("not an effect");
        };
        summary(effect)
    };
    assert_eq!(
        header_of(&transcript),
        header("Searching", "the web: 'zidane'", "")
    );
    transcript.update(&json!({"entryId": "w1", "eventId": 2, "patch": [
        {"op": "replace", "path": "/state",
         "value": completed(json!({"a": {"url": "https://a.example", "title": "A"}}))}]}));
    assert_eq!(
        header_of(&transcript),
        header("Searched", "'zidane' (1 source)", "")
    );
}

#[test]
fn error_result_keeps_the_server_message() {
    let failed = effect(
        "connector_web_search.web_search",
        json!({"query": "zidane"}),
        json!({"status": "completed",
            "display": {"success": false, "verb": "Searched", "message": "boom"}}),
    );
    assert_eq!(summary(&failed), header("Searched", "boom", ""));
}
